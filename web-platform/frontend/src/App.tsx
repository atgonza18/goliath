import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Layout } from './components/layout/Layout';
import { ChatPage } from './pages/chat/ChatPage';
import { ProjectsPage } from './pages/projects/ProjectsPage';
import { ActionItemsPage } from './pages/action-items/ActionItemsPage';
import { AgentsPage } from './pages/agents/AgentsPage';
import { FilesPage } from './pages/files/FilesPage';
import { ProductionTrendsPage } from './pages/production/ProductionTrendsPage';
import { ThemesPage } from './pages/themes/ThemesPage';
import { AppBuilderPage } from './pages/app-builder/AppBuilderPage';
import { CallsPage } from './pages/calls/CallsPage';
import { CallDetailPage } from './pages/calls/CallDetailPage';
import { GoliathOSPage } from './pages/os/GoliathOS';

function App() {
  return (
    <BrowserRouter>
      <TooltipProvider delayDuration={0}>
        <Routes>
          {/* Goliath OS — full-screen desktop, no sidebar */}
          <Route path="/os" element={<GoliathOSPage />} />

          <Route element={<Layout />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/production" element={<ProductionTrendsPage />} />
            <Route path="/action-items" element={<ActionItemsPage />} />
            <Route path="/calls" element={<CallsPage />} />
            <Route path="/calls/:botId" element={<CallDetailPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/files" element={<FilesPage />} />
            <Route path="/themes" element={<ThemesPage />} />
            <Route path="/app-builder" element={<AppBuilderPage />} />
          </Route>
        </Routes>
      </TooltipProvider>
    </BrowserRouter>
  );
}

export default App;
