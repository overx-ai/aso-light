import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "@/components/Layout";
import ProtectedRoute from "@/components/ProtectedRoute";
import LoginPage from "@/pages/LoginPage";
import RegisterPage from "@/pages/RegisterPage";
import DashboardPage from "@/pages/DashboardPage";
import CredentialsPage from "@/pages/CredentialsPage";
import PricingPage from "@/pages/PricingPage";
import KeywordsPage from "@/pages/KeywordsPage";
import AvailabilityPage from "@/pages/AvailabilityPage";
import MetadataPage from "@/pages/MetadataPage";
import CrossLocalizationPage from "@/pages/CrossLocalizationPage";
import ReviewsPage from "@/pages/ReviewsPage";
import VisibilityPage from "@/pages/VisibilityPage";
import AsoCheckPage from "@/pages/AsoCheckPage";
import SettingsPage from "@/pages/SettingsPage";
import RevenueCatSettingsPage from "@/pages/RevenueCatSettingsPage";
import RevenueCatEntitlementsPage from "@/pages/RevenueCatEntitlementsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route index element={<DashboardPage />} />
          <Route path="credentials" element={<CredentialsPage />} />
          <Route path="apps/:id/pricing" element={<PricingPage />} />
          <Route path="apps/:id/keywords" element={<KeywordsPage />} />
          <Route path="apps/:id/availability" element={<AvailabilityPage />} />
          <Route path="apps/:id/metadata" element={<MetadataPage />} />
          <Route
            path="apps/:id/cross-localization"
            element={<CrossLocalizationPage />}
          />
          <Route path="apps/:id/reviews" element={<ReviewsPage />} />
          <Route path="apps/:id/visibility" element={<VisibilityPage />} />
          <Route path="apps/:id/aso-check" element={<AsoCheckPage />} />
          <Route
            path="apps/:id/revenuecat"
            element={<Navigate to="entitlements" replace />}
          />
          <Route
            path="apps/:id/revenuecat/settings"
            element={<RevenueCatSettingsPage />}
          />
          <Route
            path="apps/:id/revenuecat/entitlements"
            element={<RevenueCatEntitlementsPage />}
          />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
