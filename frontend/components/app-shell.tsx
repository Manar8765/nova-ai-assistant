"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { LogoutButton } from "../app/dashboard/logout-button";

function Icon({ name }: { name: "grid" | "file" | "spark" | "settings" | "help" | "plus" | "arrow" | "trash" }) {
  const paths: Record<string, string> = {
    grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    file: "M6 3h8l4 4v14H6zM14 3v5h5M9 13h6M9 17h6",
    spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z",
    settings: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.1h-2.4v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H6v-2.4h.8a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L8 8.6l1.7-1.7.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.1h2.4v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1 1.7 1.7-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.1V14h-.1a1.7 1.7 0 0 0-1.6 1z",
    help: "M9.6 9a2.5 2.5 0 1 1 4.4 1.6c-.9 1.1-2 1.3-2 2.9M12 17.5h.01M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
    plus: "M12 5v14M5 12h14",
    arrow: "M5 12h13M13 7l5 5-5 5",
    trash: "M5 7h14M10 11v6M14 11v6M8 7l1-3h6l1 3m-9 0 1 14h10l1-14",
  };
  return <svg aria-hidden="true" className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}

export function AppShell({ children, title, description, userName, companyName }: {
  children: ReactNode;
  title: string;
  description?: string;
  userName?: string | null;
  companyName?: string | null;
}) {
  const pathname = usePathname();
  const initial = (userName ?? "Nova User").trim().charAt(0).toUpperCase() || "N";
  const navItems = [
    { href: "/dashboard", label: "Dashboard", icon: "grid" as const },
    { href: "/documents", label: "Documents", icon: "file" as const },
    { href: "/chat", label: "AI Assistant", icon: "spark" as const },
  ];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/dashboard" className="brand" aria-label="Nova home"><span className="brand-mark">N</span><span>nova</span></Link>
        <nav aria-label="Primary navigation" className="primary-nav">
          <p className="nav-label">Workspace</p>
          {navItems.map((item) => {
            const active = item.href === "/chat" ? pathname === "/chat" : pathname === item.href;
            return <Link key={item.href} href={item.href} className={`nav-item${active ? " active" : ""}`}><Icon name={item.icon} />{item.label}</Link>;
          })}
          <Link href="/chat" className={`nav-item${pathname.startsWith("/chat/") ? " active" : ""}`}><Icon name="spark" />Conversations</Link>
        </nav>
        <div className="sidebar-secondary">
          <p className="nav-label">Support</p>
          <span className="nav-item nav-item-muted"><Icon name="settings" />Settings</span>
          <span className="nav-item nav-item-muted"><Icon name="help" />Help center</span>
        </div>
        <div className="sidebar-account">
          <div className="avatar">{initial}</div>
          <div className="account-copy"><strong>{userName || "Nova User"}</strong><span>{companyName || "Workspace"}</span></div>
          <LogoutButton />
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar"><div className="mobile-brand"><span className="brand-mark">N</span> nova</div><div className="topbar-meta">Workspace overview <span className="status-dot" /> All systems operational</div></header>
        <main className="page-content"><div className="page-heading"><div><p className="eyebrow">Nova workspace</p><h1>{title}</h1>{description ? <p className="page-description">{description}</p> : null}</div></div>{children}</main>
      </div>
    </div>
  );
}

export { Icon };
