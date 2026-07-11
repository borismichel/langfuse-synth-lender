import { redirect } from "next/navigation";
import { Boxes, Plus } from "lucide-react";

import { auth } from "@/auth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { fetchUseCases } from "@/lib/catalog";

export default async function CatalogPage() {
  const session = await auth();

  if (!session) {
    redirect("/auth/signin");
  }

  const useCases = await fetchUseCases(session);

  return (
    <main className="min-h-screen bg-background">
      <header className="border-b bg-card">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div>
            <p className="text-sm font-medium text-muted-foreground">
              Langfuse Lender Portal
            </p>
            <h1 className="text-2xl font-semibold tracking-normal">Use cases</h1>
          </div>
          <div className="text-right text-sm text-muted-foreground">
            <p>{session.user.name}</p>
            <p>{session.user.email}</p>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-6 py-10">
        {useCases.length === 0 ? (
          <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed bg-muted/40 p-8">
            <div className="max-w-md text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-md bg-primary text-primary-foreground">
                <Boxes aria-hidden="true" className="h-6 w-6" />
              </div>
              <h2 className="text-xl font-semibold">No use cases yet</h2>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                The catalog is ready. New Langfuse use cases will appear here
                after they are added through the portal API.
              </p>
              <Button className="mt-6" disabled>
                <Plus aria-hidden="true" className="h-4 w-4" />
                Add use case
              </Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {useCases.map((useCase) => (
              <Card key={useCase.id}>
                <CardHeader>
                  <CardTitle>{useCase.title}</CardTitle>
                  {useCase.description ? (
                    <CardDescription>{useCase.description}</CardDescription>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <Button variant="outline" disabled>
                    Open
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
