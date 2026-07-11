import { ShieldCheck } from "lucide-react";

import { SignInButton } from "@/components/sign-in-button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default function SignInPage() {
  const domain = process.env.GOOGLE_WORKSPACE_DOMAIN;

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted px-6 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <ShieldCheck aria-hidden="true" className="h-5 w-5" />
          </div>
          <CardTitle>Langfuse Lender Portal</CardTitle>
          <CardDescription>
            Access is restricted to verified Google Workspace accounts
            {domain ? ` on ${domain}` : ""}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SignInButton />
        </CardContent>
      </Card>
    </main>
  );
}
