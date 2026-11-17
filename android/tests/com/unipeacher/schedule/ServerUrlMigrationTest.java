package com.unipeacher.schedule;

public final class ServerUrlMigrationTest {
    public static void main(String[] args) {
        String target = "https://0910162.xyz:9443/";
        check(null, target, target);
        check("", target, target);
        check("   ", target, target);
        check("https://schedule.unipeacher.email", target, target);
        check("https://schedule.unipeacher.email/", target, target);
        check("http://schedule.unipeacher.email/", target, target);
        check("https://schedule.unipeacher.email:443/", target, target);
        check("https://schedule.unipeacher.email:8443/", target, target);
        check(target, target, target);
        check("https://example.com/custom/", target, "https://example.com/custom/");
        check("https://schedule.unipeacher.email:9443/", target,
                "https://schedule.unipeacher.email:9443/");
        check("https://schedule.unipeacher.email/custom", target,
                "https://schedule.unipeacher.email/custom");
        System.out.println("Server URL migration: 12 cases passed");
    }

    private static void check(String input, String target, String expected) {
        String actual = ServerUrlMigration.migrate(input, target);
        if (!expected.equals(actual)) {
            throw new AssertionError("Unexpected migration result for " + input);
        }
    }
}
