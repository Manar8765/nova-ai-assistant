import { redirect } from "next/navigation";
import { createClient } from "../../../lib/supabase/server";
import { ChatAssistant } from "../../../components/chat-assistant";

export default async function ConversationPage({
  params
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const supabase = await createClient();
  const {
    data: { user },
    error
  } = await supabase.auth.getUser();
  if (error || !user) redirect("/login");
  const { conversationId } = await params;
  return <ChatAssistant initialConversationId={conversationId} />;
}
