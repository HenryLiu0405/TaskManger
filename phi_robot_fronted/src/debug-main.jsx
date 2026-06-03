import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider, theme } from 'antd';
import DeveloperConsole from './DeveloperConsole.jsx';
import './index.css';

ReactDOM.createRoot(document.getElementById('app')).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#d4a853',
          colorInfo: '#d4a853',
          colorSuccess: '#7fb87c',
          colorWarning: '#d4a853',
          colorError: '#d44a4a',
          colorTextBase: '#c8c8c8',
          colorBgBase: '#0b0b10',
          colorBgContainer: '#111118',
          colorBorder: '#2a2a2a',
          borderRadius: 2,
          fontFamily: "'JetBrains Mono', 'SF Mono', 'Cascadia Code', ui-monospace, monospace",
          fontSize: 15,
        },
      }}
    >
      <DeveloperConsole />
    </ConfigProvider>
  </React.StrictMode>
);
