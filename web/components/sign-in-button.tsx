import { LogIn } from "lucide-react";

import { signIn } from "@/auth";
import { Button } from "@/components/ui/button";

export function SignInButton() {
  return (
    <form
      action={async () => {
        "use server";
        await signIn("google", { redirectTo: "/" });
      }}
    >
      <Button type="submit">
        <LogIn aria-hidden="true" className="h-4 w-4" />
        Sign in with Google
      </Button>
    </form>
  );
}
