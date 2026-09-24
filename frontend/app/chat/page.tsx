import { redirect } from "next/navigation";
import { createClient } from "../../lib/supabase/server";
import { ChatAssistant } from "../../components/chat-assistant";

export default async function ChatPage() {
  const supabase = await createClient();
  const {
    data: { user },
    error
  } = await supabase.auth.getUser();

  if (error || !user) {
    redirect("/login");
  }

  // Any authenticated user of the company (including the End User role) may
  // ask questions; the backend resolves the company from the session profile.
  return <ChatAssistant />;
}