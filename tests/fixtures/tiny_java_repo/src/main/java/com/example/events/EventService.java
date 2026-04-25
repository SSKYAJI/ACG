package com.example.events;

import com.example.config.DatabaseConfig;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Comparator;
import java.util.List;

public class EventService {
    private final JdbcTemplate jdbcTemplate;

    public EventService(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<String> findUpcoming() {
        return null;
    }

    public Comparator<String> ordering() {
        return new Comparator<String>() {
            @Override
            public int compare(String a, String b) {
                return a.compareTo(b);
            }
        };
    }

    private void helper() {}
}
