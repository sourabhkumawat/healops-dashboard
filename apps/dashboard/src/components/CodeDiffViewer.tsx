'use client';

import React from 'react';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';

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
  return (
    <div className="h-full overflow-auto text-sm border rounded-md bg-[#2d2d2d]">
      <ReactDiffViewer
        oldValue={oldCode}
        newValue={newCode}
        splitView={splitView}
        compareMethod={DiffMethod.WORDS}
        useDarkTheme={true}
        styles={{
          variables: {
            dark: {
              diffViewerBackground: '#1e1e1e',
              diffViewerTitleBackground: '#252526',
              gutterBackground: '#1e1e1e',
              gutterColor: '#858585',
              addedBackground: '#203424',
              addedColor: '#e2e2e2',
              removedBackground: '#3e2021',
              removedColor: '#e2e2e2',
              wordAddedBackground: '#2ea043',
              wordRemovedBackground: '#da3633',
            },
          },
          lineNumber: {
            color: '#858585',
          },
          contentText: {
            color: '#d4d4d4',
            fontFamily: 'Consolas, "Courier New", monospace',
          },
        }}
        leftTitle="Original"
        rightTitle="Modified"
      />
    </div>
  );
};

export default CodeDiffViewer;
