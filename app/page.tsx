import { BackendStatus } from "@/src/components/backend-status";

export default function Home() {
  return (
    <div className="site-shell">
      <header className="topbar"><div className="brand-mark">G</div><div><strong>Gharpayy</strong><span>Local Auto Sync 100X</span></div><div className="phase-label">PHASE 1 · FOUNDATION</div></header>
      <main className="hero">
        <div className="eyebrow">OPERATIONS INFRASTRUCTURE</div>
        <h1>Local-first systems for <em>reliable</em> growth.</h1>
        <p className="hero-copy">A secure foundation for the next generation of Gharpayy operations. Frontend, API, and database layers are ready for the product work ahead.</p>
        <BackendStatus />
        <div className="foundation-grid"><div><span>01</span><h2>Next.js frontend</h2><p>App Router, TypeScript, Tailwind, and tested UI foundations.</p></div><div><span>02</span><h2>FastAPI backend</h2><p>Local API with structured configuration and health checks.</p></div><div><span>03</span><h2>MySQL ready</h2><p>SQLAlchemy sessions and Alembic migrations prepared for later.</p></div></div>
      </main>
      <footer><span>GHARPAYY / LOCAL AUTO SYNC</span><span>OCR and workflow features arrive in a later phase</span></footer>
    </div>
  );
}
