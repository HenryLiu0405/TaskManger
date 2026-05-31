import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';

import App from './App.jsx';
import './index.css';

const neoTheme = {
  token: {
    colorPrimary: "#4682B4",
    borderRadius: 0,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    colorBgBase: "#ffffff",
    colorTextBase: "#2F4F4F",
    wireframe: true,
    colorSuccess: "#00CC00",
    colorInfo: "#87CEEB",
  },
  components: {
    Button: {
      borderRadius: 0,
      controlHeight: 40,
      defaultBorderColor: "#2F4F4F",
      defaultColor: "#2F4F4F",
      primaryColor: "#FFFFFF",
      contentFontSize: 16,
      fontWeight: 600,
    },
    Input: {
      borderRadius: 0,
      colorBorder: "#2F4F4F",
      activeBorderColor: "#4682B4",
      hoverBorderColor: "#87CEEB",
    },
    Card: {
      borderRadius: 0,
      colorBorder: "#2F4F4F",
    },
  },
};

ReactDOM.createRoot(document.getElementById('app')).render(
  <React.StrictMode>
    <ConfigProvider theme={neoTheme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
