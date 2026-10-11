import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { NewRelease } from "./pages/NewRelease";
import { ReleaseDetail } from "./pages/ReleaseDetail";
import { Releases } from "./pages/Releases";
import "./styles.css";

function App() {
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <img src="/favicon.svg" alt="" width={22} height={22} />
          ReleaseWatch
        </NavLink>
        <nav>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/releases" end>
            Releases
          </NavLink>
          <NavLink to="/releases/new">Register release</NavLink>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/releases" element={<Releases />} />
          <Route path="/releases/new" element={<NewRelease />} />
          <Route path="/releases/:id" element={<ReleaseDetail />} />
          <Route path="*" element={<p className="empty">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
