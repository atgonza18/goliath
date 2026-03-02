import { Outlet } from 'react-router-dom';
import { SidebarProvider, SidebarInset } from '@/components/ui/sidebar';
import { AppSidebar } from './Sidebar';

export function Layout() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="overflow-hidden min-h-0">
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  );
}
