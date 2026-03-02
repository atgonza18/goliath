import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Layout } from './components/layout/Layout';
import { ChatPage } from './pages/chat/ChatPage';
import { ProjectsPage } from './pages/projects/ProjectsPage';
import { ActionItemsPage } from './pages/action-items/ActionItemsPage';
import { AgentsPage } from './pages/agents/AgentsPage';
import { FilesPage } from './pages/files/FilesPage';

function App() {
  return (
    <BrowserRouter>
      <TooltipProvider delayDuration={0}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/action-items" element={<ActionItemsPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/files" element={<FilesPage />} />
          </Route>
        </Routes>
      </TooltipProvider>
    </BrowserRouter>
  );
}

export default App;
