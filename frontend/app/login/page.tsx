import { AuthForm } from "../../components/auth-form";

type LoginPageProps = {
  searchParams: Promise<{ error?: string }>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error } = await searchParams;
  const initialError =
    error === "confirmation_failed"
      ? "We could not confirm your email. Request a new confirmation link and try again."
      : undefined;

  return <AuthForm mode="sign-in" initialError={initialError} />;
}
