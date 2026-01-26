'use client';

import { useState, useEffect, useRef } from 'react';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2 } from 'lucide-react';
import {
    getCurrentUser,
    updateUserProfile,
    type CurrentUser
} from '@/actions/auth';

export function ProfileSettings() {
    const [user, setUser] = useState<CurrentUser | null>(null);
    const [userName, setUserName] = useState('');
    const [organizationName, setOrganizationName] = useState('');
    const [savingProfile, setSavingProfile] = useState(false);
    const [loadingUser, setLoadingUser] = useState(true);
    const [profileMessage, setProfileMessage] = useState<{
        type: 'success' | 'error';
        text: string;
    } | null>(null);

    const fetchingUserRef = useRef(false);

    // Fetch user data
    useEffect(() => {
        const fetchUser = async () => {
            if (fetchingUserRef.current) return; // Prevent duplicate calls
            fetchingUserRef.current = true;
            setLoadingUser(true);
            try {
                const currentUser = await getCurrentUser();
                if (currentUser) {
                    setUser(currentUser);
                    setUserName(currentUser.name || '');
                    setOrganizationName(currentUser.organization_name || '');
                }
            } finally {
                setLoadingUser(false);
                fetchingUserRef.current = false;
            }
        };
        fetchUser();
    }, []);

    const handleSaveProfile = async () => {
        setSavingProfile(true);
        setProfileMessage(null);

        const result = await updateUserProfile({
            name: userName,
            organization_name: organizationName
        });

        if (result.success && result.user) {
            setUser(result.user);
            setProfileMessage({
                type: 'success',
                text: 'Profile updated successfully!'
            });
            // Clear message after 3 seconds
            setTimeout(() => setProfileMessage(null), 3000);
        } else {
            setProfileMessage({
                type: 'error',
                text: result.message || 'Failed to update profile'
            });
        }

        setSavingProfile(false);
    };

    return (
        <Card className="border-zinc-800 bg-zinc-900">
            <CardHeader>
                <CardTitle className="text-zinc-100">
                    Profile Settings
                </CardTitle>
                <CardDescription className="text-zinc-400">
                    Update your personal information
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {loadingUser ? (
                    <div className="flex items-center justify-center py-12">
                        <Loader2 className="h-8 w-8 animate-spin text-zinc-400" />
                        <span className="ml-3 text-zinc-400">
                            Loading profile...
                        </span>
                    </div>
                ) : (
                    <>
                        {profileMessage && (
                            <Alert
                                className={
                                    profileMessage.type === 'success'
                                        ? 'bg-green-900/20 border-green-700'
                                        : 'bg-red-900/20 border-red-700'
                                }
                            >
                                <AlertDescription
                                    className={
                                        profileMessage.type === 'success'
                                            ? 'text-green-300'
                                            : 'text-red-300'
                                    }
                                >
                                    {profileMessage.text}
                                </AlertDescription>
                            </Alert>
                        )}
                        <div className="grid gap-2">
                            <Label htmlFor="name" className="text-zinc-100">
                                Display Name
                            </Label>
                            <Input
                                id="name"
                                value={userName}
                                onChange={(e) => setUserName(e.target.value)}
                                placeholder="Enter your display name"
                                className="bg-zinc-800 border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="email" className="text-zinc-100">
                                Email Address
                            </Label>
                            <Input
                                id="email"
                                value={user?.email || ''}
                                disabled
                                className="bg-zinc-800 border-zinc-700 text-zinc-100 opacity-50 cursor-not-allowed"
                            />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="org-name" className="text-zinc-100">
                                Organization Name
                            </Label>
                            <Input
                                id="org-name"
                                value={organizationName}
                                onChange={(e) =>
                                    setOrganizationName(e.target.value)
                                }
                                placeholder="Enter your organization name"
                                className="bg-zinc-800 border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <Button
                            onClick={handleSaveProfile}
                            disabled={savingProfile}
                            className="bg-green-600 hover:bg-green-700"
                        >
                            {savingProfile ? (
                                <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    Saving...
                                </>
                            ) : (
                                'Save Changes'
                            )}
                        </Button>
                    </>
                )}
            </CardContent>
        </Card>
    );
}
