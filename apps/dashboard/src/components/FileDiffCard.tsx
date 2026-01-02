'use client';

import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ChevronDown, ChevronRight, FileText } from 'lucide-react';
import CodeDiffViewer from './CodeDiffViewer';

interface FileDiffCardProps {
    filename: string;
}

export default function FileDiffCard({ filename }: FileDiffCardProps) {
    const [isExpanded, setIsExpanded] = useState(true);

    // Mock data generation (preserving logic from page.tsx)
    const oldCode = `// Original content of ${filename}\nfunction example() {\n  return "error";\n}`;
    const newCode = `// Fixed content of ${filename}\nfunction example() {\n  return "success";\n}`;
    const language = filename.split('.').pop() || 'javascript';

    return (
        <div className="border rounded-md overflow-hidden bg-background mb-4 last:mb-0 shadow-sm">
            <div
                className="flex items-center justify-between p-3 bg-zinc-900/50 cursor-pointer hover:bg-zinc-800/50 transition-colors"
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-2">
                    <Button variant="ghost" size="icon" className="h-6 w-6 p-0 text-muted-foreground">
                        {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </Button>
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-mono text-zinc-300">{filename}</span>
                </div>
                <div className="flex items-center gap-2">
                     {/* Placeholder for future diff stats like +10 -5 */}
                </div>
            </div>

            {isExpanded && (
                <div className="border-t border-zinc-800">
                    <CodeDiffViewer
                        oldCode={oldCode}
                        newCode={newCode}
                        language={language}
                        splitView={true}
                    />
                </div>
            )}
        </div>
    );
}
