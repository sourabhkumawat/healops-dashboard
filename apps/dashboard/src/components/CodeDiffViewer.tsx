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
    <div className="h-full overflow-auto text-sm border rounded-md">
      <ReactDiffViewer
        oldValue={oldCode}
        newValue={newCode}
        splitView={splitView}
        compareMethod={DiffMethod.WORDS}
        styles={{
          variables: {
            light: {
              diffViewerBackground: '#f8f9fa',
              diffViewerTitleBackground: '#fafbfc',
              addedBackground: '#e6ffed',
              addedColor: '#24292e',
              removedBackground: '#ffeef0',
              removedColor: '#24292e',
              wordAddedBackground: '#acf2bd',
              wordRemovedBackground: '#fdb8c0',
            },
            dark: {
              diffViewerBackground: '#2d2d2d',
              diffViewerTitleBackground: '#333333',
              addedBackground: '#044b53',
              addedColor: 'white',
              removedBackground: '#632b30',
              removedColor: 'white',
              wordAddedBackground: '#055d67',
              wordRemovedBackground: '#7d383f',
            },
          },
        }}
        leftTitle="Original"
        rightTitle="Modified"
      />
    </div>
  );
};

export default CodeDiffViewer;
