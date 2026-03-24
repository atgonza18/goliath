import { NavLink, useLocation } from 'react-router-dom';
import { MessageSquare, FolderKanban, BarChart3, CheckSquare, Bot, FolderOpen, Palette, Hammer, Monitor, Phone } from 'lucide-react';
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
} from '@/components/ui/sidebar';
import { ConversationList } from '@/pages/chat/ConversationList';
import { useChatContext } from '@/pages/chat/ChatContext';
import { useTheme } from '@/contexts/ThemeContext';
import { SwarmIndicator } from '@/components/SwarmIndicator';

const navItems = [
  { to: '/', label: 'CHAT', icon: MessageSquare, color: 'var(--chart-1)' },
  { to: '/projects', label: 'PROJECTS', icon: FolderKanban, color: 'var(--chart-2)' },
  { to: '/production', label: 'PRODUCTION', icon: BarChart3, color: 'var(--chart-3)' },
  { to: '/action-items', label: 'ACTIONS', icon: CheckSquare, color: 'var(--chart-4)' },
  { to: '/calls', label: 'CALLS', icon: Phone, color: 'var(--chart-5)' },
  { to: '/agents', label: 'AGENTS', icon: Bot, color: 'var(--chart-1)' },
  { to: '/files', label: 'FILES', icon: FolderOpen, color: 'var(--chart-1)' },
  { to: '/app-builder', label: 'APP BUILDER', icon: Hammer, color: 'var(--chart-2)' },
  { to: '/os', label: 'GOLIATH OS', icon: Monitor, color: 'var(--chart-3)' },
];

export function AppSidebar() {
  const location = useLocation();
  const chatCtx = useChatContext();
  const isOnChat = location.pathname === '/';
  const { themeDefinition } = useTheme();

  return (
    <ShadcnSidebar collapsible="icon">
      <SidebarHeader className="p-4" style={{ borderBottom: '2px solid var(--theme-border)' }}>
        <div className="flex items-center gap-2.5 group-data-[collapsible=icon]:justify-center">
          <div
            className="flex h-8 w-8 shrink-0 items-center justify-center"
            style={{ background: 'var(--theme-logo-bg)', border: '2px solid var(--theme-accent)' }}
          >
            <span className="text-[12px] font-bold" style={{ color: 'var(--theme-accent)' }}>G</span>
          </div>
          <div className="group-data-[collapsible=icon]:hidden">
            <span
              className="text-[13px] font-bold"
              style={{ color: 'var(--theme-text-secondary)', letterSpacing: '0.12em' }}
            >
              GOLIATH
            </span>
          </div>
        </div>
      </SidebarHeader>

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
                      <NavLink
                        to={item.to}
                        style={
                          isActive
                            ? { borderLeft: `3px solid ${item.color}`, paddingLeft: '9px' }
                            : { borderLeft: '3px solid transparent', paddingLeft: '9px' }
                        }
                      >
                        <item.icon
                          className="size-4"
                          style={isActive ? { color: item.color } : {}}
                        />
                        <span className="text-[12px] font-bold tracking-wider">{item.label}</span>
                        {item.to === '/' && chatCtx?.hasActiveStreams && (
                          <span className="flex items-center gap-1 ml-auto">
                            <span
                              className="w-2 h-2 shrink-0"
                              style={{
                                background: 'var(--chart-3)',
                                borderRadius: '50%',
                                animation: 'pulse 1.5s ease-in-out infinite',
                              }}
                            />
                            {chatCtx.activeStreamIds.length > 1 && (
                              <span
                                className="text-[9px] font-bold"
                                style={{ color: 'var(--chart-3)' }}
                              >
                                {chatCtx.activeStreamIds.length}
                              </span>
                            )}
                          </span>
                        )}
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}

              {/* ─── THEMES nav item ─────────────────────────────────────── */}
              <SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={location.pathname === '/themes'}
                  tooltip="THEMES"
                >
                  <NavLink
                    to="/themes"
                    style={
                      location.pathname === '/themes'
                        ? { borderLeft: `3px solid var(--theme-accent)`, paddingLeft: '9px' }
                        : { borderLeft: '3px solid transparent', paddingLeft: '9px' }
                    }
                  >
                    <Palette
                      className="size-4"
                      style={location.pathname === '/themes' ? { color: 'var(--theme-accent)' } : {}}
                    />
                    <span className="text-[12px] font-bold tracking-wider">THEMES</span>
                    {/* Current theme accent swatch — visible in expanded sidebar */}
                    <span
                      className="ml-auto w-3 h-3 shrink-0 group-data-[collapsible=icon]:hidden"
                      style={{ background: themeDefinition.accent, border: '1px solid var(--theme-border)' }}
                    />
                  </NavLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {isOnChat && chatCtx && (
          <SidebarGroup className="group-data-[collapsible=icon]:hidden flex-1 min-h-0 p-0">
            <div style={{ borderTop: '2px solid var(--theme-border)' }} className="flex flex-col flex-1 min-h-0 h-full overflow-hidden">
              <ConversationList
                conversations={chatCtx.conversations}
                activeId={chatCtx.activeConversationId}
                onSelect={chatCtx.onSelectConversation}
                onNewChat={chatCtx.onNewChat}
              />
            </div>
          </SidebarGroup>
        )}
      </SidebarContent>

      <SidebarFooter className="group-data-[collapsible=icon]:px-1">
        {/* Swarm activity indicator — visible when agents are dispatched in parallel */}
        <div className="group-data-[collapsible=icon]:hidden" style={{ borderBottom: '1px solid var(--theme-border-subtle)' }}>
          <SwarmIndicator />
        </div>

        <div className="flex items-center gap-2 px-2 py-1 group-data-[collapsible=icon]:justify-center">
          <div className="h-2 w-2 shrink-0" style={{ background: 'var(--theme-status-dot)' }} />
          <span
            className="text-[10px] font-bold tracking-widest group-data-[collapsible=icon]:hidden"
            style={{ color: 'var(--theme-text-dim)' }}
          >
            {chatCtx?.hasActiveStreams
              ? `${chatCtx.activeStreamIds.length} STREAM${chatCtx.activeStreamIds.length > 1 ? 'S' : ''} ACTIVE`
              : 'ONLINE'}
          </span>
        </div>
      </SidebarFooter>
    </ShadcnSidebar>
  );
}
