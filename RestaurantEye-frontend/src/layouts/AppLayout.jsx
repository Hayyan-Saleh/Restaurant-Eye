import { Outlet } from 'react-router-dom'
import {
  SidebarProvider,
  SidebarInset,
  SidebarTrigger,
} from '@/components/ui/sidebar'
import { AdminSidebar } from '@/components/ui/admin-sidebar'
import { Toaster } from '@/components/ui/toaster'

function AppLayout() {
  return (
    <SidebarProvider>
      <AdminSidebar />
      <SidebarInset>
        <div className="mr-4 mt-1">
          <SidebarTrigger />
        </div>
        
        <div className="flex-1 p-6 overflow-auto">
          <Outlet />
        </div>
      </SidebarInset>

      <Toaster />
    </SidebarProvider>
  )
}

export default AppLayout