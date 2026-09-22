import { redirect } from "next/navigation";
import { createClient } from "../../lib/supabase/server";
import { DocumentsManager } from "../../components/documents-manager";

export default async function DocumentsPage() {
  const supabase = await createClient();
  const {
    data: { user },
    error: userError,
  } = await supabase.auth.getUser();

  if (userError || !user) {
    redirect("/login");
  }

  const { data: profile } = await supabase
    .from("user")
    .select("role")
    .eq("user_id", user.id)
    .maybeSingle();

  if (profile?.role?.toLowerCase() !== "admin") {
    redirect("/dashboard");
  }

  return <DocumentsManager />;
}
