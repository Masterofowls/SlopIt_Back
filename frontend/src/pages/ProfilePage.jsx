import React, { useEffect, useRef, useState } from "react";
import { useUser, UserButton } from "@clerk/clerk-react";
import { useNavigate } from "react-router-dom";
import { useProtectedApi } from "../hooks/useProtectedApi";
import FrogBackground from "../components/ToxicBackground";
import "./ProfilePage.css";

const clean = (s) =>
  s && !/^user\d+$/i.test(s) ? s : null;

const ProfilePage = () => {
  const { user, isLoaded } = useUser();
  const { get, patch } = useProtectedApi();
  const navigate = useNavigate();
  const previewUrlRef = useRef(null);
  const [profile, setProfile] = useState(null);
  const [posts, setPosts] = useState([]);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editBio, setEditBio] = useState("");
  const [editAvatar, setEditAvatar] = useState(null);
  const [avatarPreview, setAvatarPreview] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isLoaded)
      get("/me/")
        .then(setProfile)
        .catch(() => {});
  }, [isLoaded, get]);
  useEffect(() => {
    if (profile?.username)
      get(`/users/${profile.username}/posts/`)
        .then((d) => setPosts(Array.isArray(d) ? d : (d.results ?? [])))
        .catch(() => {});
  }, [profile?.username, get]);

  const displayName =
    profile?.display_name ||
    clean(user?.fullName) ||
    profile?.username ||
    "ANON";
  const avatarUrl =
    profile?.avatar_url ||
    user?.imageUrl ||
    "../../../dist/background-green.png";
  const revoke = () => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
  };
  const openEdit = () => {
    setEditName(profile?.display_name || "");
    setEditBio(profile?.bio || "");
    setEditing(true);
  };
  const cancelEdit = () => {
    revoke();
    setEditing(false);
    setEditAvatar(null);
    setAvatarPreview(null);
  };
  const onAvatarChange = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    revoke();
    setEditAvatar(f);
    previewUrlRef.current = URL.createObjectURL(f);
    setAvatarPreview(previewUrlRef.current);
  };
  const saveProfile = async () => {
    setSaving(true);
    try {
      const isFile = Boolean(editAvatar);
      const d = isFile
        ? ((fd) => {
            fd.append("display_name", editName.trim());
            fd.append("bio", editBio.trim());
            fd.append("avatar", editAvatar);
            return fd;
          })(new FormData())
        : { display_name: editName.trim(), bio: editBio.trim() };
      const cfg = isFile ? { headers: { "Content-Type": undefined } } : {};
      setProfile(await patch("/me/", d, cfg));
      cancelEdit();
    } finally {
      setSaving(false);
    }
  };

  if (!isLoaded)
    return (
      <div>
        <FrogBackground />
        <p>Loading...</p>
      </div>
    );

  return (
    <div className="pp-page">
      <FrogBackground />
      <button className="pp-back-btn" onClick={() => navigate(-1)}>
        ← Back
      </button>
      <div className="pp-container">
        <div className="pp-card pp-header-card">
          <div className="pp-avatar-wrap">
            <div className="pp-avatar-glow"></div>
            {avatarUrl ? (
              <img src={avatarUrl} alt="avatar" className="pp-avatar" />
            ) : (
              <div className="pp-avatar-placeholder">
                {displayName[0].toUpperCase()}
              </div>
            )}
          </div>
          <div className="pp-identity">
            <h1 className="pp-displayname">{displayName}</h1>
            {profile?.username && (
              <p className="pp-username">@{profile.username}</p>
            )}
            {profile?.bio && <p className="pp-bio">{profile.bio}</p>}
          </div>
          <div className="pp-stats">
            <div className="pp-stat">
              <span className="pp-stat-val">{posts.length}</span>
              <span className="pp-stat-label">Posts</span>
            </div>
            <div className="pp-clerk-btn">
              <UserButton afterSignOutUrl="/" />
            </div>
            <button
              className="pp-edit-toggle"
              onClick={editing ? cancelEdit : openEdit}
            >
              {editing ? "Close" : "Edit"}
            </button>
          </div>
        </div>
        {editing && (
          <div className="pp-card pp-edit-panel">
            <h2 className="pp-edit-title">Edit Profile</h2>
            <div className="pp-edit-avatar-row">
              <div className="pp-edit-avatar-preview">
                {avatarPreview || avatarUrl ? (
                  <img src={avatarPreview || avatarUrl} alt="avatar" />
                ) : (
                  <div className="pp-edit-avatar-placeholder">
                    {displayName[0].toUpperCase()}
                  </div>
                )}
              </div>
              <label className="pp-edit-upload-btn">
                Choose Avatar
                <input
                  type="file"
                  accept="image/*"
                  onChange={onAvatarChange}
                  style={{ display: "none" }}
                />
              </label>
            </div>
            <div className="pp-edit-field">
              <label className="pp-edit-label">Display Name</label>
              <input
                className="pp-edit-input"
                maxLength={100}
                placeholder="Enter your display name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
              />
            </div>
            <div className="pp-edit-field">
              <label className="pp-edit-label">Bio</label>
              <textarea
                className="pp-edit-input pp-edit-textarea"
                maxLength={500}
                placeholder="Tell the world"
                value={editBio}
                onChange={(e) => setEditBio(e.target.value)}
                rows={3}
              />
            </div>
            <div className="pp-edit-actions">
              <button
                className="pp-edit-btn pp-edit-btn--primary"
                onClick={saveProfile}
                disabled={saving}
              >
                {saving ? "Saving..." : "Save"}
              </button>
              <button
                className="pp-edit-btn pp-edit-btn--cancel"
                onClick={cancelEdit}
                disabled={saving}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="pp-posts-section">
        <div className="pp-posts-grid">
          {posts.map((p) => (
            <div
              key={p.id}
              className="pp-post-card"
              onClick={() => p.slug && navigate(`/post/${p.slug}`)}
            >
              {p.media?.[0]?.file && (
                <div className="pp-post-thumb-wrap">
                  <img
                    src={p.media[0].file}
                    alt={p.title}
                    className="pp-post-thumb"
                  />
                </div>
              )}
              <div className="pp-post-info">
                <span
                  className={`pp-post-kind pp-kind--${p.kind?.toLowerCase()}`}
                >
                  {p.kind}
                </span>
                <p className="pp-post-title">{p.title}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ProfilePage;
