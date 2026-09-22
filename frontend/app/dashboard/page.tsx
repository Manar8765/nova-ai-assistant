import { redirect } from "next/navigation";
import Link from "next/link";
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

  const { data: profile, error: profileError } = await supabase
    .from("user")
    .select("full_name, role, company_id")
    .eq("user_id", user.id)
    .maybeSingle();

  const { data: company, error: companyError } = profile
    ? await supabase
        .from("company")
        .select("name")
        .eq("company_id", profile.company_id)
        .maybeSingle()
    : { data: null, error: null };

  const accountDetailsAvailable = !profileError && !companyError && profile && company;

  return (
    <main>
      <h1>Dashboard</h1>
      <section aria-labelledby="account-heading">
        <h2 id="account-heading">Account</h2>
        <p>Email: {user.email ?? "Unavailable"}</p>
        {accountDetailsAvailable ? (
          <>
            <p>Full name: {profile.full_name ?? "Unavailable"}</p>
            <p>Role: {profile.role ?? "Unavailable"}</p>
            <p>Company: {company.name}</p>
          </>
        ) : (
          <div role="status">
            {profileError ? <p>Profile error: {profileError.message}</p> : null}
            {!profile ? <p>Profile row not found</p> : null}
            {companyError ? <p>Company error: {companyError.message}</p> : null}
            {profile && !company ? <p>Company row not found</p> : null}
          </div>
        )}
      </section>
      <p>
        <Link href="/documents">Manage documents</Link>
      </p>
      <LogoutButton />
    </main>
  );
}
