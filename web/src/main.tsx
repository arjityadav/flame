import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { RequireAuth } from "./auth";
import { Shell } from "./components/Shell";
import { Accuracy } from "./pages/Accuracy";
import { Account } from "./pages/Account";
import { Agents } from "./pages/Agents";
import { Ask } from "./pages/Ask";
import { Changes, ChangesIndex } from "./pages/Changes";
import { GraphPage } from "./pages/Graph";
import { History } from "./pages/History";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { NewAnalysis } from "./pages/NewAnalysis";
import { Profile } from "./pages/Profile";
import { Requirements } from "./pages/Requirements";
import { Runs } from "./pages/Runs";
import { Upgrade } from "./pages/Upgrade";
import { View3D } from "./pages/View3D";
import "./styles/app.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  {
    element: <RequireAuth />,
    children: [
      { path: "/", element: <Landing /> },
      {
        element: <Shell />,
        children: [
          { path: "/account", element: <Account /> },
          { path: "/history", element: <History /> },
          { path: "/new", element: <NewAnalysis /> },
          { path: "/new/:runId", element: <NewAnalysis /> },
          { path: "/runs", element: <Runs /> },
          { path: "/changes", element: <ChangesIndex /> },
          { path: "/changes/:caseId", element: <Changes /> },
          { path: "/runs/:runId", element: <Requirements /> },
          { path: "/runs/:runId/profile", element: <Profile /> },
          { path: "/runs/:runId/3d", element: <View3D /> },
          { path: "/runs/:runId/agents", element: <Agents /> },
          { path: "/runs/:runId/accuracy", element: <Accuracy /> },
          { path: "/runs/:runId/upgrade", element: <Upgrade /> },
          { path: "/runs/:runId/ask", element: <Ask /> },
          { path: "/runs/:runId/graph", element: <GraphPage view="structure" key="structure" /> },
          { path: "/runs/:runId/graph/meaning", element: <GraphPage view="meaning" key="meaning" /> },
        ],
      },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
