import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';

import App from './App.jsx';
import './index.css';

const theme = {
  token: {
    colorPrimary: "#ffffff",
    borderRadius: 0,
    fontFamily: '"Helvetica Neue", Arial, sans-serif',
    colorBgBase: "#000000",
    colorTextBase: "#ffffff",
    colorSuccess: "rgba(255,255,255,0.7)",
    colorInfo: "rgba(255,255,255,0.5)",
    colorWarning: "rgba(255,255,255,0.5)",
    colorError: "rgba(255,255,255,0.3)",
  },
};

ReactDOM.createRoot(document.getElementById('app')).render(
  <React.StrictMode>
    <ConfigProvider theme={theme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
