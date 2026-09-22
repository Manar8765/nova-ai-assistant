import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "../../../lib/supabase/server";

export async function GET(request: NextRequest) {
  const tokenHash = request.nextUrl.searchParams.get("token_hash");
  const type = request.nextUrl.searchParams.get("type") as EmailOtpType | null;
  const redirectUrl = request.nextUrl.clone();

  redirectUrl.pathname = "/dashboard";
  redirectUrl.search = "";

  if (tokenHash && type) {
    const supabase = await createClient();
    const { data, error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type });

    if (!error) {
      const companyName = String(data.user?.user_metadata.company_name ?? "");
      const fullName = String(data.user?.user_metadata.full_name ?? "");
      const { error: onboardingError } = await supabase.rpc("create_company_admin", {
        p_company_name: companyName,
        p_full_name: fullName
      });

      if (!onboardingError) {
        return NextResponse.redirect(redirectUrl);
      }

      redirectUrl.pathname = "/login";
      redirectUrl.searchParams.set("error", "onboarding_failed");
      return NextResponse.redirect(redirectUrl);
    }
  }

  redirectUrl.pathname = "/login";
  redirectUrl.searchParams.set("error", "confirmation_failed");
  return NextResponse.redirect(redirectUrl);
}
