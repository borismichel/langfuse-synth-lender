import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const dynamic = "force-dynamic";

export default function AuthErrorPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-muted px-6 py-12">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-destructive text-destructive-foreground">
            <AlertTriangle aria-hidden="true" className="h-5 w-5" />
          </div>
          <CardTitle>Access restricted</CardTitle>
          <CardDescription>
            This account is not allowed to access the portal.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline">
            <Link href="/auth/signin">Try another account</Link>
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
