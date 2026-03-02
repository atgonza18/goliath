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
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-foreground/5">
            <span className="text-xs font-bold tracking-tight text-foreground">G</span>
          </div>
          <span className="text-sm font-semibold tracking-tight text-foreground group-data-[collapsible=icon]:hidden">
            GOLIATH
          </span>
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
          <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          <span className="text-[11px] text-muted-foreground group-data-[collapsible=icon]:hidden">
            v1.0
          </span>
        </div>
      </SidebarFooter>
    </ShadcnSidebar>
  );
}
