import { connection } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

async function fetchHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}/health`, { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}

export async function ApiStatus() {
  await connection();
  const healthy = await fetchHealth();
  return (
    <p className="text-sm text-zinc-500">
      API: {healthy ? "online" : "unreachable (it may be waking up)"}
    </p>
  );
}
