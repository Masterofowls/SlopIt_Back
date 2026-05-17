import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { UserButton, SignIn } from "@clerk/clerk-react";
import { useAuthContext } from "../../context/AuthContext";
import PostCreateModal from "../posts/PostCreateModal";
import "./Navigation.css";

const Navigation = () => {
  const navigate = useNavigate();
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showPostModal, setShowPostModal] = useState(false);
  const { isAuthenticated, isLoading } = useAuthContext();

  const searchRef = useRef(null);

  const ADMIN_URL =
    (import.meta.env.VITE_API_URL || "https://slopit-api.fly.dev") + "/admin/";

  function renderUserArea() {
    if (isLoading) return null;
    if (!isAuthenticated) {
      return (
        <>
          <button
            className="login-button"
            onClick={() => setShowAuthModal(true)}
          >
            Login
          </button>

          <button className="nav-profile" onClick={() => navigate("/profile")}>
            Profile
          </button>
        </>
      );
    }
    return (
      <>
        <button className="new-post-btn" onClick={() => setShowPostModal(true)}>
          + Post
        </button>
        <div className="nav-user-actions">
          <a
            href={ADMIN_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="manage-button"
            aria-label="Manage"
          >
            <span className="manage-icon" aria-hidden="true">
              ⚙
            </span>
            <span className="manage-label">Manage</span>
          </a>
          <UserButton afterSignOutUrl="/" />
          <button className="nav-profile" onClick={() => navigate("/profile")}>
            Profile
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <nav className="navigation">
        <div className="nav-container">
          <div className="nav-brand" onClick={() => navigate("/home")}>
            <h1>slopit</h1>
          </div>

          <form
            className="nav-search-form"
            onSubmit={(e) => {
              e.preventDefault();
              const q = searchRef.current?.value.trim();
              if (q) {
                navigate(`/home?q=${encodeURIComponent(q)}`);
              } else {
                navigate('/home');
              }
              searchRef.current?.blur();
            }}
          >
            <input
              ref={searchRef}
              className="nav-search-input"
              type="search"
              placeholder="search posts or authors…"
              aria-label="Search posts"
              onChange={(e) => {
                if (!e.target.value) {
                  navigate('/home');
                }
              }}
            />
            <button className="nav-search-btn" type="submit">
              🔍
            </button>
          </form>

          <div className="nav-user">{renderUserArea()}</div>
        </div>
      </nav>

      {showPostModal && (
        <PostCreateModal onClose={() => setShowPostModal(false)} />
      )}

      {showAuthModal && !isAuthenticated && (
        <div
          className="nav-auth-overlay"
          onClick={() => setShowAuthModal(false)}
        >
          <div className="nav-auth-modal" onClick={(e) => e.stopPropagation()}>
            <SignIn
              routing="virtual"
              fallbackRedirectUrl="/home"
              appearance={{
                variables: {
                  colorPrimary: "#00ff00",
                  colorBackground: "#001400",
                  colorText: "#00ff00",
                  colorTextSecondary: "#00cc00",
                  colorInputBackground: "#002200",
                  colorInputText: "#00ff00",
                  colorNeutral: "#00aa00",
                  borderRadius: "4px",
                  fontFamily: '"Courier New", Courier, monospace',
                  fontSize: "14px",
                },
                elements: {
                  card: "slop-clerk-card",
                  rootBox: "nav-clerk-root",
                },
              }}
            />

            <button
              className="nav-auth-close"
              onClick={() => setShowAuthModal(false)}
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </>
  );
};

export default Navigation;
