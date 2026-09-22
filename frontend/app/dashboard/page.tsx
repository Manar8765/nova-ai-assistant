import { redirect } from "next/navigation";
import { createClient } from "../../lib/supabase/server";
import { LogoutButton } from "./logout-button";

export default async function DashboardPage() {
  const supabase = await createClient();
  const {
    data: { user },
    error
  } = await supabase.auth.getUser();

  if (error || !user) {
    redirect("/login");
  }

  return (
    <main>
      <h1>Dashboard</h1>
      <p>Signed in as {user.email ?? "your account"}.</p>
      <LogoutButton />
    </main>
  );
}
