import { useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

const Icons = {
  dashboard: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="1.5" y="1.5" width="5" height="5" rx="1.5"/><rect x="9.5" y="1.5" width="5" height="5" rx="1.5"/>
      <rect x="1.5" y="9.5" width="5" height="5" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1.5"/>
    </svg>
  ),
  search: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="6.5" cy="6.5" r="4"/><path d="M10 10l3.5 3.5"/>
    </svg>
  ),
  network: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="8" cy="3.5" r="1.5"/><circle cx="3" cy="12.5" r="1.5"/><circle cx="13" cy="12.5" r="1.5"/>
      <path d="M8 5v3.5M6.6 10.8l-2 1.2M9.4 10.8l2 1.2"/>
    </svg>
  ),
  cases: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4.5 3h7a1 1 0 0 1 1 1v8.5a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>
      <path d="M5.5 6.5h5M5.5 9h3.5"/>
    </svg>
  ),
  patterns: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="1.5,12 4.5,7.5 7.5,9.5 10.5,4.5 14,7"/>
      <path d="M10.5 4.5h3v3"/>
    </svg>
  ),
  alerts: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1.5L1.5 13.5h13L8 1.5z"/><line x1="8" y1="6" x2="8" y2="9"/><circle cx="8" cy="11.5" r=".5" fill="currentColor"/>
    </svg>
  ),
  biometric: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="8" cy="5.5" r="2.5"/><path d="M2 14c0-3 2.7-5.5 6-5.5s6 2.5 6 5.5"/>
    </svg>
  ),
  ingestion: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1.5v9M5 7.5l3 3 3-3"/><path d="M2.5 13.5h11"/>
    </svg>
  ),
  admin: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="2"/>
      <path d="M8 1.5v1M8 13.5v1M1.5 8h1M13.5 8h1M3.4 3.4l.7.7M11.9 11.9l.7.7M3.4 12.6l.7-.7M11.9 4.1l.7-.7"/>
    </svg>
  ),
  logout: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 13.5H3.5a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1H6M10.5 11l3-3-3-3M13.5 8H6.5"/>
    </svg>
  ),
};

const ArgusLogo = () => (
  <svg className="sidebar-brand-eye" viewBox="0 0 28 28" fill="none">
    <circle cx="14" cy="14" r="12" stroke="rgba(99,102,241,.25)" strokeWidth="1"/>
    <circle cx="14" cy="14" r="8"  stroke="rgba(99,102,241,.5)"  strokeWidth="1"/>
    <circle cx="14" cy="14" r="4"  stroke="#818cf8" strokeWidth="1.2"/>
    <circle cx="14" cy="14" r="2"  fill="#6366f1"/>
    <line x1="14" y1="2"  x2="14" y2="5"  stroke="rgba(99,102,241,.5)" strokeWidth="1"/>
    <line x1="14" y1="23" x2="14" y2="26" stroke="rgba(99,102,241,.5)" strokeWidth="1"/>
    <line x1="2"  y1="14" x2="5"  y2="14" stroke="rgba(99,102,241,.5)" strokeWidth="1"/>
    <line x1="23" y1="14" x2="26" y2="14" stroke="rgba(99,102,241,.5)" strokeWidth="1"/>
  </svg>
);

const SECTIONS = [
  {
    label: "Investigate",
    links: [
      { to: "/",        label: "Dashboard",       icon: Icons.dashboard, end: true },
      { to: "/search",  label: "Search",          icon: Icons.search },
      { to: "/network", label: "Network Map",     icon: Icons.network },
      { to: "/cases",   label: "Cases",           icon: Icons.cases },
    ],
  },
  {
    label: "Intelligence",
    links: [
      { to: "/patterns", label: "Patterns",       icon: Icons.patterns },
      { to: "/alerts",   label: "Alerts",         icon: Icons.alerts },
      { to: "/hunt",     label: "Biometric Hunt", icon: Icons.biometric },
    ],
  },
  {
    label: "Data",
    links: [
      { to: "/ingestion", label: "Ingestion",     icon: Icons.ingestion },
    ],
  },
  {
    label: "System",
    links: [
      { to: "/security", label: "Security", icon: Icons.admin },
      { to: "/admin", label: "Admin", icon: Icons.admin, roles: ["admin"] },
    ],
  },
];

export default function Nav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  // Phones get a slide-in menu: close it after navigating, or on Escape.
  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  function signOut() {
    logout();
    navigate("/", { replace: true });
  }
  if (!user) return null;

  const initials = user.full_name
    .split(" ")
    .slice(0, 2)
    .map((n) => n[0])
    .join("")
    .toUpperCase();

  const roleColor: Record<string, string> = {
    admin: "var(--red)",
    supervisor: "var(--amber)",
    analyst: "var(--green)",
    investigator: "var(--accent-2)",
  };

  return (
    <>
      <div className="mobile-bar">
        <button type="button" className="mobile-menu-btn" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen((v) => !v)}>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            {open ? <path d="M4 4l12 12M16 4L4 16" /> : <path d="M3 5h14M3 10h14M3 15h14" />}
          </svg>
        </button>
        <span className="mobile-brand">ARGUS</span>
      </div>
      {open && <div className="sidebar-backdrop" onClick={() => setOpen(false)} aria-hidden="true" />}
    <nav id="main-nav" className={`sidebar ${open ? "sidebar-open" : ""}`} aria-label="Main">
      {/* Brand */}
      <div className="sidebar-brand">
        <div className="sidebar-brand-logo">
          <ArgusLogo />
          <div>
            <div className="sidebar-brand-mark">ARGUS</div>
            <span className="sidebar-brand-sub">Intel Platform</span>
          </div>
        </div>
        <span className="sidebar-classif">CLASSIFIED · EYES ONLY</span>
      </div>

      {/* Nav sections */}
      {SECTIONS.map((section) => {
        const visible = section.links.filter(
          (l) => !("roles" in l) || (l as { roles?: string[] }).roles?.includes(user.role)
        );
        if (!visible.length) return null;
        return (
          <div key={section.label} className="sidebar-section">
            <p className="sidebar-section-label">{section.label}</p>
            <div className="sidebar-links">
              {visible.map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  end={"end" in l ? l.end : false}
                  className={({ isActive }) => isActive ? "sidebar-link active" : "sidebar-link"}
                >
                  {l.icon}
                  {l.label}
                </NavLink>
              ))}
            </div>
          </div>
        );
      })}


      {/* User */}
      <div className="sidebar-user">
        <div className="sidebar-user-inner">
          <div className="sidebar-avatar">{initials}</div>
          <div>
            <div className="sidebar-user-name">{user.full_name}</div>
            <div className="sidebar-user-meta" style={{ color: roleColor[user.role] ?? "var(--text-3)" }}>
              {user.role}
            </div>
          </div>
        </div>
        <button className="sidebar-logout" onClick={signOut}>
          {Icons.logout}
          Sign out
        </button>
      </div>
    </nav>
    </>
  );
}
