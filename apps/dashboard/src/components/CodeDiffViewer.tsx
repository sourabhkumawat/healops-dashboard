'use client';

import React from 'react';
import { DiffEditor } from '@monaco-editor/react';

interface CodeDiffViewerProps {
  oldCode: string;
  newCode: string;
  language?: string;
  splitView?: boolean;
}

const CodeDiffViewer: React.FC<CodeDiffViewerProps> = ({
  oldCode,
  newCode,
  language = 'javascript',
  splitView = true,
}) => {

  // Simple mapping for common extensions to Monaco languages
  const getMonacoLanguage = (lang: string) => {
    const map: Record<string, string> = {
      'js': 'javascript',
      'jsx': 'javascript',
      'ts': 'typescript',
      'tsx': 'typescript',
      'py': 'python',
      'rb': 'ruby',
      'go': 'go',
      'java': 'java',
      'cpp': 'cpp',
      'c': 'c',
      'html': 'html',
      'css': 'css',
      'json': 'json',
      'md': 'markdown',
      'yml': 'yaml',
      'yaml': 'yaml',
      'sql': 'sql',
      'sh': 'shell',
      'bash': 'shell',
    };
    return map[lang.toLowerCase()] || lang;
  };

  const monacoLanguage = getMonacoLanguage(language);

  return (
    <div className="h-[500px] w-full border rounded-md bg-[#1e1e1e] overflow-hidden">
      <DiffEditor
        height="100%"
        original={oldCode}
        modified={newCode}
        language={monacoLanguage}
        theme="vs-dark"
        options={{
          renderSideBySide: splitView,
          readOnly: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 14,
          wordWrap: 'on',
          automaticLayout: true,
        }}
      />
    </div>
  );
};

export default CodeDiffViewer;
