import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { MessageSquare, FolderKanban, BarChart3, CheckSquare, Bot, FolderOpen, Palette, Check } from 'lucide-react';
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
import { useTheme, THEMES } from '@/contexts/ThemeContext';

const navItems = [
  { to: '/', label: 'CHAT', icon: MessageSquare, color: 'var(--chart-1)' },
  { to: '/projects', label: 'PROJECTS', icon: FolderKanban, color: 'var(--chart-2)' },
  { to: '/production', label: 'PRODUCTION', icon: BarChart3, color: 'var(--chart-3)' },
  { to: '/action-items', label: 'ACTIONS', icon: CheckSquare, color: 'var(--chart-4)' },
  { to: '/agents', label: 'AGENTS', icon: Bot, color: 'var(--chart-5)' },
  { to: '/files', label: 'FILES', icon: FolderOpen, color: 'var(--chart-1)' },
];

export function AppSidebar() {
  const location = useLocation();
  const chatCtx = useChatContext();
  const isOnChat = location.pathname === '/';
  const { theme, setTheme, themeDefinition } = useTheme();
  const [themePickerOpen, setThemePickerOpen] = useState(false);

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
                  isActive={themePickerOpen}
                  tooltip="THEMES"
                  onClick={() => setThemePickerOpen((v) => !v)}
                  style={
                    themePickerOpen
                      ? {
                          borderLeft: `3px solid var(--theme-accent)`,
                          paddingLeft: '9px',
                          cursor: 'pointer',
                        }
                      : { borderLeft: '3px solid transparent', paddingLeft: '9px', cursor: 'pointer' }
                  }
                >
                  <Palette
                    className="size-4"
                    style={themePickerOpen ? { color: 'var(--theme-accent)' } : {}}
                  />
                  <span className="text-[12px] font-bold tracking-wider">THEMES</span>
                  {/* Current theme accent swatch — visible in expanded sidebar */}
                  <span
                    className="ml-auto w-3 h-3 shrink-0 group-data-[collapsible=icon]:hidden"
                    style={{ background: themeDefinition.accent, border: '1px solid var(--theme-border)' }}
                  />
                </SidebarMenuButton>

                {/* Inline theme picker — only visible when expanded sidebar + picker open */}
                {themePickerOpen && (
                  <div
                    className="group-data-[collapsible=icon]:hidden animate-expand"
                    style={{
                      borderLeft: '3px solid var(--theme-accent)',
                      marginLeft: '12px',
                      marginTop: '2px',
                    }}
                  >
                    {THEMES.map((t) => {
                      const isActive = t.name === theme;
                      return (
                        <button
                          key={t.name}
                          className="w-full flex items-center gap-2 px-3 py-2 text-left transition-none"
                          style={{
                            background: isActive ? 'var(--theme-accent-dim)' : 'transparent',
                            borderBottom: '1px solid var(--theme-border-subtle)',
                            cursor: 'pointer',
                          }}
                          onClick={() => {
                            setTheme(t.name);
                            setThemePickerOpen(false);
                          }}
                        >
                          {/* Swatch */}
                          <span
                            className="shrink-0 flex items-center justify-center"
                            style={{
                              width: '24px',
                              height: '24px',
                              background: t.bg,
                              border: `2px solid ${isActive ? t.accent : 'var(--theme-border)'}`,
                              position: 'relative',
                            }}
                          >
                            <span
                              style={{
                                width: '10px',
                                height: '10px',
                                background: t.accent,
                                display: 'block',
                              }}
                            />
                          </span>

                          {/* Labels */}
                          <span className="flex-1 min-w-0">
                            <span
                              className="block text-[10px] font-bold tracking-widest"
                              style={{
                                color: isActive ? 'var(--theme-accent)' : 'var(--theme-text-secondary)',
                              }}
                            >
                              {t.label}
                            </span>
                            <span
                              className="block text-[9px] tracking-wider"
                              style={{ color: 'var(--theme-text-muted)' }}
                            >
                              {t.description}
                            </span>
                          </span>

                          {/* Active indicator */}
                          {isActive && (
                            <Check
                              className="shrink-0"
                              style={{
                                width: '12px',
                                height: '12px',
                                color: 'var(--theme-accent)',
                              }}
                            />
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {isOnChat && chatCtx && (
          <SidebarGroup className="group-data-[collapsible=icon]:hidden flex-1 min-h-0">
            <div style={{ borderTop: '2px solid var(--theme-border)' }} className="flex-1 min-h-0 overflow-hidden">
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
