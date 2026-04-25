package com.example.accounts;

import com.example.config.DatabaseConfig;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

public class AccountRepository {
    private final JdbcTemplate jdbcTemplate;

    public AccountRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public RowMapper<String> rowMapper() {
        return new RowMapper<String>() {
            @Override
            public String mapRow(java.sql.ResultSet rs, int rowNum) {
                return null;
            }
        };
    }

    public String findById(long id) {
        return null;
    }

    interface NestedHelper {
        void doIt();
    }
}
