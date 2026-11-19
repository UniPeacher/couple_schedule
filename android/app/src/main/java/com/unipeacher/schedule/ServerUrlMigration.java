package com.unipeacher.schedule;

final class ServerUrlMigration {
    private ServerUrlMigration() {
    }

    static String migrate(String savedUrl, String defaultUrl) {
        if (savedUrl == null || savedUrl.trim().isEmpty()) return defaultUrl;
        String address = savedUrl.trim();
        while (address.endsWith("/")) {
            address = address.substring(0, address.length() - 1);
        }
        if (address.equals("https://schedule.unipeacher.email")
                || address.equals("http://schedule.unipeacher.email")
                || address.equals("https://schedule.unipeacher.email:443")
                || address.equals("https://schedule.unipeacher.email:8443")) {
            return defaultUrl;
        }
        return savedUrl;
    }
}
