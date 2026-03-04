import { NavLink, useLocation } from 'react-router-dom';
import { MessageSquare, FolderKanban, CheckSquare, Bot, FolderOpen } from 'lucide-react';
import {
  Sidebar as ShadcnSidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from '@/components/ui/sidebar';

const navItems = [
  { to: '/', label: 'Chat', icon: MessageSquare },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/action-items', label: 'Action Items', icon: CheckSquare },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/files', label: 'Files', icon: FolderOpen },
];

export function AppSidebar() {
  const location = useLocation();

  return (
    <ShadcnSidebar collapsible="icon">
      <SidebarHeader className="p-4">
        <div className="flex items-center gap-2.5 group-data-[collapsible=icon]:justify-center">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-zinc-800 border border-zinc-700/50">
            <span className="text-[11px] font-bold tracking-tight text-zinc-300">G</span>
          </div>
          <div className="group-data-[collapsible=icon]:hidden">
            <span className="text-[13px] font-semibold tracking-tight text-foreground">
              Goliath
            </span>
          </div>
        </div>
      </SidebarHeader>

      <SidebarSeparator />

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => {
                const isActive =
                  item.to === '/'
                    ? location.pathname === '/'
                    : location.pathname.startsWith(item.to);

                return (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      isActive={isActive}
                      tooltip={item.label}
                    >
                      <NavLink to={item.to}>
                        <item.icon className="size-4" />
                        <span>{item.label}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="group-data-[collapsible=icon]:px-1">
        <div className="flex items-center gap-2 px-2 py-1 group-data-[collapsible=icon]:justify-center">
          <div className="h-1.5 w-1.5 rounded-full bg-emerald-500/70" />
          <span className="text-[10px] text-zinc-600 group-data-[collapsible=icon]:hidden">
            Online
          </span>
        </div>
      </SidebarFooter>
    </ShadcnSidebar>
  );
}
