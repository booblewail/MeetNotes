"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut, Settings } from "lucide-react";
import { setStoredToken } from "@/lib/api";

export function AppHeader({ userEmail }: { userEmail?: string }) {
  const router = useRouter();

  const handleSignOut = () => {
    setStoredToken(null);
    router.push("/login");
  };

  return (
    <header className="no-print sticky top-0 z-20 border-b border-border bg-surface-elevated/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link
          href="/"
          className="inline-flex items-center gap-2.5 text-[15px] font-semibold tracking-tight text-ink hover:opacity-80"
        >
          <span
            aria-hidden="true"
            className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-ink text-surface-elevated"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4 fill-current">
              <rect x="3" y="7" width="2.2" height="6" rx="1.1" />
              <rect x="7" y="4" width="2.2" height="12" rx="1.1" />
              <rect x="11" y="6" width="2.2" height="8" rx="1.1" />
              <rect x="15" y="8" width="2.2" height="4" rx="1.1" />
            </svg>
          </span>
          <span>MeetNotes</span>
        </Link>

        <nav aria-label="Primary navigation" className="flex items-center gap-1 sm:gap-2">
          {userEmail && (
            <span className="hidden pr-2 text-xs text-ink-muted sm:inline">
              {userEmail}
            </span>
          )}
          <Link
            href="/settings"
            className="inline-flex min-h-[36px] items-center gap-1.5 rounded-full px-3 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <Settings className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Settings</span>
          </Link>
          <button
            type="button"
            onClick={handleSignOut}
            className="inline-flex min-h-[36px] items-center gap-1.5 rounded-full px-3 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="hidden sm:inline">Sign out</span>
          </button>
        </nav>
      </div>
    </header>
  );
}
