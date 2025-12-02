'use client';

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuShortcut,
    DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { logoutAction, type CurrentUser } from '@/actions/auth';

interface UserNavProps {
    user: CurrentUser | null;
}

export function UserNav({ user }: UserNavProps) {
    // Get initials from email or name
    const getInitials = (email: string) => {
        const parts = email.split('@')[0].split(/[._-]/);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[1][0]).toUpperCase();
        }
        return email.substring(0, 2).toUpperCase();
    };

    // Get display name from email
    const getDisplayName = (email: string) => {
        const namePart = email.split('@')[0];
        // Capitalize first letter of each word
        return namePart
            .split(/[._-]/)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ');
    };

    const displayName = user ? getDisplayName(user.email) : 'User';
    const initials = user ? getInitials(user.email) : 'U';
    const email = user?.email || 'user@healops.ai';
    const role = user?.role || 'user';

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button
                    variant="outline"
                    className="relative h-8 w-8 rounded-full border-zinc-700"
                >
                    <Avatar className="h-8 w-8">
                        <AvatarImage src="/avatars/01.png" alt={displayName} />
                        <AvatarFallback className="bg-[#A0F1DA] text-black">
                            {initials}
                        </AvatarFallback>
                    </Avatar>
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="w-56" align="end" forceMount>
                <DropdownMenuLabel className="font-normal">
                    <div className="flex flex-col space-y-1">
                        <p className="text-sm font-medium leading-none">
                            {displayName}
                        </p>
                        <p className="text-xs leading-none text-muted-foreground">
                            {email}
                        </p>
                        {role && (
                            <p className="text-xs leading-none text-muted-foreground capitalize">
                                {role}
                            </p>
                        )}
                    </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => logoutAction()}>
                    Log out
                    <DropdownMenuShortcut>⇧⌘Q</DropdownMenuShortcut>
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
