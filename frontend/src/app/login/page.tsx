"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Eye, EyeOff } from "lucide-react";
import { ApiError, api, setStoredToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res =
        mode === "login"
          ? await api.login({ email, password })
          : await api.register({ email, password, full_name: fullName });
      setStoredToken(res.access_token);
      router.push("/");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Authentication failed."
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 py-12">
      <div className="w-full max-w-[400px] rounded-3xl border border-border bg-surface-elevated p-8 shadow-lg">
        <div className="flex flex-col items-center text-center">
          <span
            aria-hidden="true"
            className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-ink text-surface-elevated shadow-xs"
          >
            <svg viewBox="0 0 20 20" className="h-5 w-5 fill-current">
              <rect x="3" y="7" width="2.2" height="6" rx="1.1" />
              <rect x="7" y="4" width="2.2" height="12" rx="1.1" />
              <rect x="11" y="6" width="2.2" height="8" rx="1.1" />
              <rect x="15" y="8" width="2.2" height="4" rx="1.1" />
            </svg>
          </span>
          <h1 className="mt-4 text-xl font-semibold tracking-[-0.02em] text-ink">
            {mode === "login" ? "Sign in to MeetNotes" : "Create your account"}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            Live meeting notes with structured recall
          </p>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-1 rounded-full bg-surface-muted p-1">
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setError(null);
            }}
            className={`min-h-[36px] rounded-full text-xs font-medium ${
              mode === "login"
                ? "bg-surface-elevated text-ink shadow-xs"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("register");
              setError(null);
            }}
            className={`min-h-[36px] rounded-full text-xs font-medium ${
              mode === "register"
                ? "bg-surface-elevated text-ink shadow-xs"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            Create Account
          </button>
        </div>

        <form
          method="POST"
          onSubmit={(e) => void handleSubmit(e)}
          className="mt-6 space-y-4"
        >
          {mode === "register" && (
            <div className="flex flex-col gap-1">
              <label
                htmlFor="full-name"
                className="text-[13px] font-medium text-ink"
              >
                Full name <span aria-hidden="true">*</span>
              </label>
              <input
                id="full-name"
                name="name"
                type="text"
                autoComplete="name"
                enterKeyHint="next"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="mn-field min-h-[46px] rounded-xl px-3.5 text-base"
              />
              <div className="field-error-msg">Please enter your name.</div>
            </div>
          )}

          <div className="flex flex-col gap-1">
            <label htmlFor="email" className="text-[13px] font-medium text-ink">
              Email <span aria-hidden="true">*</span>
            </label>
            <span id="email-hint" className="text-xs text-ink-muted">
              you@example.com
            </span>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              spellCheck={false}
              autoCapitalize="none"
              enterKeyHint="next"
              aria-describedby="email-hint"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mn-field min-h-[46px] rounded-xl px-3.5 text-base"
            />
            <div className="field-error-msg">
              Enter an email address in the format you@example.com.
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label
                htmlFor="password"
                className="text-[13px] font-medium text-ink"
              >
                Password <span aria-hidden="true">*</span>
              </label>
              <button
                type="button"
                aria-pressed={showPassword}
                aria-label={showPassword ? "Hide password" : "Show password"}
                onClick={() => setShowPassword((v) => !v)}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-ink-muted hover:text-ink"
              >
                {showPassword ? (
                  <>
                    <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                    Hide
                  </>
                ) : (
                  <>
                    <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                    Show
                  </>
                )}
              </button>
            </div>
            <span id="password-hint" className="text-xs text-ink-muted">
              Minimum 6 characters
            </span>
            <input
              id="password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              minLength={6}
              enterKeyHint="done"
              aria-describedby="password-hint"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mn-field min-h-[46px] rounded-xl px-3.5 text-base"
            />
            <div className="field-error-msg">
              Password must be at least 6 characters.
            </div>
          </div>

          {error && (
            <div
              role="alert"
              className="rounded-xl bg-red-500/10 px-3.5 py-2.5 text-[13px] text-red-700 dark:text-red-300"
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="min-h-[46px] w-full rounded-full bg-accent px-4 text-sm font-medium text-white shadow-xs hover:opacity-90 disabled:opacity-60"
          >
            {submitting
              ? mode === "login"
                ? "Signing in…"
                : "Creating account…"
              : mode === "login"
              ? "Sign In"
              : "Create Account"}
          </button>
        </form>
      </div>
    </main>
  );
}
