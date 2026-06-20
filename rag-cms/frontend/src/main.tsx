import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './AuthContext';
import { ThemeProvider } from './ThemeContext';
import { BindToastBridge, ToastProvider } from './ToastContext';
import Login from './pages/Login';
import RagList from './pages/RagList';
import RagDetail from './pages/RagDetail';
import RagChat from './pages/RagChat';
import AdminUsers from './pages/AdminUsers';
import RequireAuth from './RequireAuth';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <BindToastBridge />
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route element={<App />}>
                <Route path="login" element={<Login />} />
                <Route
                  index
                  element={
                    <RequireAuth>
                      <RagList />
                    </RequireAuth>
                  }
                />
                <Route
                  path="rag/:id"
                  element={
                    <RequireAuth>
                      <RagDetail />
                    </RequireAuth>
                  }
                />
                <Route
                  path="rag/:id/chat"
                  element={
                    <RequireAuth>
                      <RagChat />
                    </RequireAuth>
                  }
                />
                <Route
                  path="admin/users"
                  element={
                    <RequireAuth>
                      <AdminUsers />
                    </RequireAuth>
                  }
                />
              </Route>
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </ToastProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
