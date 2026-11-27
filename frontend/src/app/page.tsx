import { Suspense } from "react";

import { ApiStatus } from "./api-status";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6">
      <h1 className="text-3xl font-semibold tracking-tight">PermiPath</h1>
      <p className="text-zinc-600 dark:text-zinc-400">
        Work in progress. The question interface is not available yet.
      </p>
      <Suspense fallback={<p className="text-sm text-zinc-500">Checking API…</p>}>
        <ApiStatus />
      </Suspense>
    </main>
  );
}
