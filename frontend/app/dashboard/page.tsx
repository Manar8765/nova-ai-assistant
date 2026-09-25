import { redirect } from "next/navigation";
import Link from "next/link";
import { createClient } from "../../lib/supabase/server";
import { DashboardOverview } from "../../components/dashboard-overview";

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

  return <><Link className="sr-only" href="/chat">Ask Nova</Link><DashboardOverview userEmail={user.email ?? undefined} userName={profileError ? null : profile?.full_name} companyName={companyError ? null : company?.name} /></>;
}
