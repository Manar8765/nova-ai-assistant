"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "../lib/supabase/client";

type AuthMode = "sign-in" | "sign-up";

type AuthFormProps = {
  mode: AuthMode;
  initialError?: string;
};

async function completeOnboarding(
  supabase: ReturnType<typeof createClient>,
  companyName: string,
  fullName: string
): Promise<string | null> {
  const { error } = await supabase.rpc("create_company_admin", {
    p_company_name: companyName,
    p_full_name: fullName
  });

  return error?.message ?? null;
}

export function AuthForm({ mode, initialError }: AuthFormProps) {
  const router = useRouter();
  const [error, setError] = useState(initialError ?? "");
  const [message, setMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSignUp = mode === "sign-up";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setIsSubmitting(true);

    const formData = new FormData(event.currentTarget);
    const email = String(formData.get("email") ?? "");
    const password = String(formData.get("password") ?? "");
    const fullName = String(formData.get("fullName") ?? "");
    const companyName = String(formData.get("companyName") ?? "");
    const supabase = createClient();

    if (isSignUp) {
      const { data, error: signUpError } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/confirm`,
          data: {
            company_name: companyName,
            full_name: fullName
          }
        }
      });

      if (signUpError) {
        setError(signUpError.message);
      } else if (data.session) {
        const onboardingError = await completeOnboarding(supabase, companyName, fullName);

        if (onboardingError) {
          setError(
            "Your account was created, but company setup could not be completed. Sign in and try again."
          );
        } else {
          router.replace("/dashboard");
          router.refresh();
        }
      } else {
        setMessage("Check your email to confirm your account, then sign in.");
      }
    } else {
      const { data, error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password
      });

      if (signInError) {
        setError(signInError.message);
      } else {
        const companyName = String(data.user?.user_metadata.company_name ?? "");
        const fullName = String(data.user?.user_metadata.full_name ?? "");
        const onboardingError = await completeOnboarding(supabase, companyName, fullName);

        if (onboardingError) {
          setError(
            "Your account is signed in, but company setup could not be completed. Please contact support."
          );
        } else {
          router.replace("/dashboard");
          router.refresh();
        }
      }
    }

    setIsSubmitting(false);
  }

  return (
    <main>
      <h1>{isSignUp ? "Create an account" : "Sign in"}</h1>
      <form onSubmit={handleSubmit}>
        {isSignUp ? (
          <>
            <label htmlFor="fullName">Full name</label>
            <input id="fullName" name="fullName" type="text" autoComplete="name" required />

            <label htmlFor="companyName">Company name</label>
            <input id="companyName" name="companyName" type="text" required />
          </>
        ) : null}

        <label htmlFor="email">Email</label>
        <input id="email" name="email" type="email" autoComplete="email" required />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete={isSignUp ? "new-password" : "current-password"}
          required
        />

        {error ? <p role="alert">{error}</p> : null}
        {message ? <p role="status">{message}</p> : null}

        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Please wait…" : isSignUp ? "Create account" : "Sign in"}
        </button>
      </form>

      <p>
        {isSignUp ? "Already have an account?" : "Need an account?"}{" "}
        <Link href={isSignUp ? "/login" : "/signup"}>
          {isSignUp ? "Sign in" : "Sign up"}
        </Link>
      </p>
    </main>
  );
}
