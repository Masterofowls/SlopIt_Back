import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate } from "react-router-dom";
import { useUser } from "@clerk/clerk-react";
import Card from "../ui/Card";
import CommentSection from "./CommentSection";
import ShareButton from "./ShareButton";
import ToxicityMeter from "./ToxicityMeter";
import BookmarkButton from "./BookmarkButton";
import { useProtectedApi } from "../../hooks/useProtectedApi";
import { useAuthContext } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import "./Post.css";

function resolveAuthorName(author) {
  if (!author) return "anon";
  const isClerkId = (s) =>
    typeof s === "string" && /^(clerk_|k_)?user_[a-z0-9]{6,}/i.test(s);
  const isPlaceholder = (s) => typeof s === "string" && /^user\d+$/i.test(s);
  const isBad = (s) => isClerkId(s) || isPlaceholder(s);

  if (author.display_name) return author.display_name;
  if (author.full_name && !isBad(author.full_name)) return author.full_name;

  const nameParts = [author.first_name, author.last_name]
    .filter(Boolean)
    .join(" ")
    .trim();
  if (nameParts) return nameParts;

  if (author.username && !isBad(author.username)) return author.username;
  if (author.name && !isBad(author.name)) return author.name;
  if (
    author.email &&
    !author.email.endsWith("@no-email.local") &&
    !isClerkId(author.email.split("@")[0])
  )
    return author.email.split("@")[0];

  return "anon";
}

const Post = ({ post, children }) => {
  const navigate = useNavigate();
  const { post: apiPost } = useProtectedApi();
  const { user: clerkUser } = useUser();
  const { telegramUser } = useAuthContext();
  const { addToast } = useToast();
  const [isAnimating, setIsAnimating] = useState(false);
  const [reportState, setReportState] = useState("idle");
  const [particles, setParticles] = useState([]);
  const [textPosition, setTextPosition] = useState({ x: 0, y: 0 });
  const [showComments, setShowComments] = useState(false);

  const likeCount = post.reaction_counts?.like ?? post.likes ?? 0;
  const commentCount = post.comment_count ?? post.comments ?? 0;

  const [localLikeCount, setLocalLikeCount] = useState(likeCount);
  const [userReaction, setUserReaction] = useState(post.user_reaction ?? null);
  const liked = userReaction === "like";

  const bodyText = post.body_markdown || post.content || "";
  const bodyHtml = post.body_html || null;
  const postContent = post.title
    ? { title: post.title, body: bodyText, bodyHtml, kind: post.kind }
    : { title: null, body: bodyText, bodyHtml: null, kind: "text" };

  const authorName = resolveAuthorName(post.author);

  const isCurrentUsersPost =
    (clerkUser &&
      (post.author?.clerk_id === clerkUser.id ||
        post.author?.username === clerkUser.username)) ||
    (telegramUser && String(post.author?.id) === String(telegramUser.id));
  const authAvatar = clerkUser?.imageUrl || telegramUser?.avatarUrl || null;
  const authorAvatar =
    post.author?.avatar_url ||
    post.author?.avatar ||
    (isCurrentUsersPost ? authAvatar : null) ||
    "/frog.png";
  const createdAt =
    post.created_at || post.timestamp || new Date().toISOString();

  const handleLikeClick = async () => {
    if (isAnimating) return;

    const wasLiked = liked;

    if (wasLiked) {
      setUserReaction(null);
      setLocalLikeCount((n) => Math.max(0, n - 1));
    } else {
      setUserReaction("like");
      setLocalLikeCount((n) => n + 1);
      setIsAnimating(true);
      const newParticles = Array.from({ length: 12 }, (_, i) => ({
        id: Date.now() + i,
        angle: (Math.PI * 2 * i) / 12,
        distance: 40 + Math.random() * 30,
        size: 8 + Math.random() * 8,
        delay: Math.random() * 0.1,
      }));
      setParticles(newParticles);
      setTextPosition({
        x: -60 + Math.random() * 120,
        y: -20 - Math.random() * 40,
      });
      setTimeout(() => {
        setIsAnimating(false);
        setParticles([]);
      }, 1000);
    }

    if (post.id && !String(post.id).startsWith("dummy")) {
      try {
        const res = await apiPost(`/posts/${post.id}/react/`, { kind: "like" });
        if (res?.reaction_counts) {
          setLocalLikeCount(res.reaction_counts.like ?? 0);
        }
        if ("user_reaction" in (res ?? {})) {
          setUserReaction(res.user_reaction);
        }
      } catch {
        setUserReaction(wasLiked ? "like" : null);
        setLocalLikeCount((n) => (wasLiked ? n + 1 : Math.max(0, n - 1)));
      }
    }
  };
  const handleReport = async () => {
    if (reportState === "done") return;
    if (reportState === "idle") {
      setReportState("confirm");
      setTimeout(
        () => setReportState((s) => (s === "confirm" ? "idle" : s)),
        4000,
      );
      return;
    }
    try {
      await apiPost(`/posts/${post.id}/report/`, { reason: "other" });
      setReportState("done");
      addToast("Post reported. Moderators will review it.", "success");
    } catch {
      setReportState("idle");
      addToast("Failed to submit report. Try again.", "error");
    }
  };

  const formatTimestamp = (timestamp) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;

    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return "Just now";
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 7) return `${days}d ago`;
    return date.toLocaleDateString();
  };

  return (
    <Card className="post">
      <div className="post-header">
        <div className="post-author">
          <img
            src={authorAvatar}
            alt={authorName}
            className="author-avatar"
            onError={(e) => {
              e.currentTarget.onerror = null;
              e.currentTarget.src = "/frog.png";
            }}
          />
          <div className="author-info">
            <span className="author-username">{authorName}</span>
            <span className="post-timestamp">{formatTimestamp(createdAt)}</span>
          </div>
        </div>
      </div>

      <div className="post-content">
        {postContent.title && (
          <p
            className={`post-title${post.slug ? " post-title--link" : ""}`}
            onClick={() => post.slug && navigate(`/post/${post.slug}`)}
            role={post.slug ? "link" : undefined}
            tabIndex={post.slug ? 0 : undefined}
            onKeyDown={(e) =>
              e.key === "Enter" && post.slug && navigate(`/post/${post.slug}`)
            }
          >
            {postContent.title}
          </p>
        )}
        {postContent.bodyHtml ? (
          <div
            className="post-text"
            dangerouslySetInnerHTML={{ __html: postContent.bodyHtml }}
          />
        ) : postContent.body ? (
          <div className="post-text post-markdown">
            <ReactMarkdown
              components={{
                a: ({ href, children }) => (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                ),
                img: ({ src, alt }) => {
                  if (!src) return null;
                  return (
                    <img
                      src={src}
                      alt={alt || ""}
                      className="post-md-image"
                      loading="lazy"
                      onError={(e) => {
                        e.currentTarget.style.display = "none";
                        console.warn("[Post] Image failed to load:", src);
                      }}
                    />
                  );
                },
                p: ({ children }) => {
                  const arr = React.Children.toArray(children);
                  if (arr.length === 1 && arr[0]?.type === "img") {
                    return <>{children}</>;
                  }
                  return <p>{children}</p>;
                },
              }}
            >
              {postContent.body}
            </ReactMarkdown>
          </div>
        ) : null}
        {postContent.kind === "link" && post.link_url && (
          <a
            className="post-link"
            href={post.link_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {post.link_url}
          </a>
        )}
      </div>

      {children}

      <div className="post-footer">
        <button
          className={`post-action${liked ? " liked" : ""}`}
          onClick={handleLikeClick}
          aria-label={liked ? "Unlike" : "Like"}
        >
          <span className="action-icon">{liked ? "❤️" : "🤍"}</span>
          <span className="action-count">{localLikeCount}</span>
          {isAnimating && (
            <>
              <span
                className="slopped-text"
                style={{
                  left: `calc(50% + ${textPosition.x}px)`,
                  top: `${textPosition.y}px`,
                }}
              >
                slopped
              </span>
              {particles.map((particle) => (
                <span
                  key={particle.id}
                  className="slop-particle"
                  style={{
                    "--angle": `${particle.angle}rad`,
                    "--distance": `${particle.distance}px`,
                    "--size": `${particle.size}px`,
                    "--delay": `${particle.delay}s`,
                  }}
                />
              ))}
            </>
          )}
        </button>
        <button
          className={`post-action${showComments ? " active" : ""}`}
          onClick={() => setShowComments((v) => !v)}
        >
          <span className="action-icon">💬</span>
          <span className="action-count">{commentCount}</span>
        </button>
        <button
          className={`post-action post-action--report${reportState === "confirm" ? " confirming" : ""}${reportState === "done" ? " reported" : ""}`}
          onClick={handleReport}
          aria-label="Report post"
          title={
            reportState === "confirm"
              ? "Click again to confirm report"
              : reportState === "done"
                ? "Reported"
                : "Report post"
          }
        >
          <span className="action-icon">
            {reportState === "done" ? "🚩" : "⚑"}
          </span>
          <span className="report-label">
            {reportState === "confirm"
              ? "CONFIRM?"
              : reportState === "done"
                ? "REPORTED"
                : "REPORT"}
          </span>
        </button>
        <ShareButton slug={post.slug} title={post.title} />
        <span className="post-view-count">👁 {post.view_count ?? 0}</span>
        <BookmarkButton
          postId={post.id}
          initialBookmarked={post.is_bookmarked ?? false}
        />
      </div>
      {/* <div className="post-toxicity">
        <ToxicityMeter
          likeCount={localLikeCount}
          dislikeCount={post.reaction_counts?.dislike ?? 0}
        />
      </div> */}

      {showComments && post.id && <CommentSection postId={post.id} />}
    </Card>
  );
};

export default Post;
