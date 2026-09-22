"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "../../lib/supabase/client";

export function LogoutButton() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [isSigningOut, setIsSigningOut] = useState(false);

  async function handleLogout() {
    setError("");
    setIsSigningOut(true);

    const { error: signOutError } = await createClient().auth.signOut();

    if (signOutError) {
      setError(signOutError.message);
      setIsSigningOut(false);
      return;
    }

    router.replace("/login");
    router.refresh();
  }

  return (
    <>
      <button type="button" onClick={handleLogout} disabled={isSigningOut}>
        {isSigningOut ? "Signing out…" : "Sign out"}
      </button>
      {error ? <p role="alert">{error}</p> : null}
    </>
  );
}
