import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider, theme } from 'antd';

import App from './App.jsx';
import './index.css';

// Lunar Command — cool cyan signal accent over deep-space glass.
const lunarTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#5cd6ee",
    colorInfo: "#5cd6ee",
    colorSuccess: "#46e6b8",
    colorError: "#ff5d76",
    colorTextBase: "#e9eef7",
    colorBgBase: "#04060d",
    borderRadius: 12,
    fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  },
  components: {
    Button: {
      controlHeight: 44,
      contentFontSize: 15,
      fontWeight: 600,
    },
  },
};

ReactDOM.createRoot(document.getElementById('app')).render(
  <React.StrictMode>
    <ConfigProvider theme={lunarTheme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
