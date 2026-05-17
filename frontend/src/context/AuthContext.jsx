import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useRef,
} from "react";
import { useUser, useClerk, useAuth } from "@clerk/clerk-react";
import { api } from "../lib/api";

const AuthContext = createContext({
  provider: null,
  isAuthenticated: false,
  isLoading: true,
  telegramUser: null,
  clerkProfile: null,
  authLogs: [],
  logout: async () => {},
});

export function AuthProvider({ children }) {
  const {
    isSignedIn: clerkSignedIn,
    isLoaded: clerkLoaded,
    user: clerkUser,
  } = useUser();
  const { signOut: clerkSignOut } = useClerk();
  const { getToken } = useAuth();

  const [telegramUser, setTelegramUser] = useState(null);
  const [telegramLoading, setTelegramLoading] = useState(true);
  const [clerkProfile, setClerkProfile] = useState(null);
  const [authLogs, setAuthLogs] = useState([]);

  function addLog(msg, data) {
    const entry = {
      t: new Date().toISOString().slice(11, 23),
      msg,
      data: data !== undefined ? JSON.stringify(data, null, 2) : null,
    };
    console.log(`[AuthContext] ${msg}`, data ?? "");
    setAuthLogs((prev) => [...prev.slice(-49), entry]);
  }

  useEffect(() => {
    addLog("clerkLoaded=" + clerkLoaded + " clerkSignedIn=" + clerkSignedIn);
  }, [clerkLoaded, clerkSignedIn]);

  useEffect(() => {
    if (!clerkLoaded || !clerkSignedIn) return;
    let cancelled = false;
    addLog("Clerk signed in — fetching backend profile from /me/");
    setTelegramLoading(false);

    getToken()
      .then((token) => {
        if (!token || cancelled) return;
        return api.get("/me/", {
          headers: { Authorization: `Bearer ${token}` },
        });
      })
      .then((res) => {
        if (!res || cancelled) return;
        const d = res.data;
        addLog("GET /me/ success", d);
        const clerkEmail = clerkUser?.primaryEmailAddress?.emailAddress ?? null;
        const isClerkId = (s) =>
          typeof s === "string" && /^(clerk_|k_)?user_[a-z0-9]{6,}/i.test(s);
        const clerkName =
          clerkUser?.fullName ||
          clerkUser?.firstName ||
          (!isClerkId(clerkUser?.username) ? clerkUser?.username : null) ||
          (clerkEmail ? clerkEmail.split("@")[0] : null);
        const backendName =
          d.display_name || d.username || (d.email ? d.email.split("@")[0] : null);
        setClerkProfile({
          username: d.username ?? null,
          email: clerkEmail ?? d.email ?? null,
          displayName: backendName || clerkName || null,
          avatarUrl:
            clerkUser?.imageUrl ?? d.avatar_url ?? d.social_avatar_url ?? null,
          bio: d.bio ?? null,
        });
      })
      .catch((err) => {
        if (cancelled) return;
        addLog("GET /me/ ERROR", {
          message: err.message,
          status: err.response?.status,
        });
      });

    return () => {
      cancelled = true;
    };
  }, [clerkLoaded, clerkSignedIn, clerkUser, getToken]);

  useEffect(() => {
    if (!clerkLoaded) return;
    if (clerkSignedIn) return;

    addLog("Clerk not signed in — probing /auth/session/");
    api
      .get("/auth/session/")
      .then(({ data, status }) => {
        addLog("GET /auth/session/ " + status, data);
        if (data.authenticated && data.user) {
          addLog("Telegram session found", data.user);
          setTelegramUser({
            id: data.user.id,
            username: data.user.username,
            email: data.user.email ?? "",
            firstName: data.user.first_name ?? "",
            lastName: data.user.last_name ?? "",
            avatarUrl: data.user.avatar_url ?? null,
          });
        } else {
          addLog("No active session in response", data);
        }
      })
      .catch((err) => {
        const detail = {
          message: err.message,
          status: err.response?.status,
          data: err.response?.data,
        };
        addLog("GET /auth/session/ ERROR", detail);
      })
      .finally(() => setTelegramLoading(false));
  }, [clerkLoaded, clerkSignedIn]);

  const logout = async () => {
    if (clerkSignedIn) {
      addLog("Logging out Clerk user");
      setClerkProfile(null);
      await clerkSignOut();
    } else {
      addLog("Logging out Telegram user");
      await api
        .post("/auth/logout/")
        .catch((err) => addLog("POST /auth/logout/ ERROR", err.message));
      setTelegramUser(null);
    }
  };

  const isLoading = !clerkLoaded || telegramLoading;
  const provider = clerkSignedIn ? "clerk" : telegramUser ? "telegram" : null;

  return (
    <AuthContext.Provider
      value={{
        provider,
        isAuthenticated: !!provider,
        isLoading,
        telegramUser,
        clerkProfile,
        authLogs,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}


export const useAuthContext = () => useContext(AuthContext);
