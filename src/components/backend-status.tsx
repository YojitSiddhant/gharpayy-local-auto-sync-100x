"use client";

import { useEffect, useState } from "react";
import { fetchBackendHealth } from "@/src/lib/api";

export function BackendStatus() {
  const [status, setStatus] = useState("Checking backend…");
  useEffect(() => {
    const controller = new AbortController();
    fetchBackendHealth(controller.signal).then((result) => setStatus(`Connected · ${result.service}`)).catch(() => setStatus("Backend not connected · start FastAPI on port 8000"));
    return () => controller.abort();
  }, []);
  return <span className="status-pill"><span className="status-dot" />{status}</span>;
}
