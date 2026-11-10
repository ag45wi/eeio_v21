-- Sanitized EEIO v2.1 RESEARCH-ONLY schema. Generated from author-provided dump.
-- Original user table and all records excluded; archived Flask login is NOT supported.
-- Do NOT restore production data into this database.
SET NAMES utf8mb4;

CREATE TABLE `co2_factor` (
  `energy_type` varchar(45) NOT NULL,
  `heat_content_hhv` float DEFAULT NULL,
  `emission_factor` float DEFAULT NULL,
  `multiplier` float DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `conversion_factor` (
  `id` varchar(45) NOT NULL,
  `energy_type` varchar(45) NOT NULL,
  `unit` varchar(25) NOT NULL,
  `multiplier_factor` float NOT NULL,
  `note` varchar(255) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `energy_lst` (
  `energy_type_code` varchar(3) NOT NULL,
  `energy_type_name` varchar(45) DEFAULT NULL,
  `name_short` varchar(4) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `fec_byyear` (
  `year` int(11) NOT NULL,
  `energy_type` varchar(45) NOT NULL,
  `fec_val` float NOT NULL,
  `note` varchar(64) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `result_emission` (
  `year` int(11) NOT NULL,
  `sector_agg` varchar(64) NOT NULL,
  `sector_code` varchar(5) NOT NULL,
  `sector_name` varchar(128) NOT NULL,
  `emissionScope1` double DEFAULT NULL,
  `emissionScope2` double DEFAULT NULL,
  `emissionScope3` double DEFAULT NULL,
  `totalEmission` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `result_emission_intensity` (
  `year` int(11) NOT NULL,
  `sector_agg` varchar(64) NOT NULL,
  `sector_code` varchar(5) NOT NULL,
  `sector_name` varchar(128) NOT NULL,
  `emissionIntensityScope1` double DEFAULT NULL,
  `emissionIntensityScope2` double DEFAULT NULL,
  `emissionIntensityScope3` double DEFAULT NULL,
  `totalEmissionIntensity` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_definition` (
  `scenario_id` int(11) NOT NULL,
  `scenario_name` varchar(100) DEFAULT NULL,
  `scenario_type_id` int(11) DEFAULT NULL,
  `scenario_year` int(11) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `scenario_signature` varchar(64) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_history_v21` (
  `scenario_history_id` int(11) NOT NULL,
  `scenario_id` int(11) DEFAULT NULL,
  `year` int(11) NOT NULL,
  `scenario_code` varchar(255) DEFAULT NULL,
  `scenario_name` varchar(255) DEFAULT NULL,
  `scenario_version` varchar(20) DEFAULT NULL,
  `scenario_source` varchar(50) DEFAULT NULL,
  `number_of_sectors` int(11) DEFAULT NULL,
  `baseline_emission` double DEFAULT NULL,
  `scenario_emission` double DEFAULT NULL,
  `emission_difference` double DEFAULT NULL,
  `percentage_change` double DEFAULT NULL,
  `baseline_scope1` double DEFAULT NULL,
  `scenario_scope1` double DEFAULT NULL,
  `baseline_scope2` double DEFAULT NULL,
  `scenario_scope2` double DEFAULT NULL,
  `baseline_scope3` double DEFAULT NULL,
  `scenario_scope3` double DEFAULT NULL,
  `created_time` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_parameter` (
  `parameter_id` int(11) NOT NULL,
  `scenario_id` int(11) NOT NULL,
  `parameter_code` varchar(100) NOT NULL,
  `parameter_value` double NOT NULL,
  `unit` varchar(30) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_parameter_history` (
  `parameter_history_id` int(11) NOT NULL,
  `scenario_history_id` int(11) NOT NULL,
  `parameter_code` varchar(100) DEFAULT NULL,
  `parameter_value` double DEFAULT NULL,
  `unit` varchar(30) DEFAULT NULL,
  `energy_type_code` varchar(3) DEFAULT NULL,
  `sector_code` varchar(4) DEFAULT NULL,
  `target_scope` varchar(50) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_sector_result` (
  `id` int(11) NOT NULL,
  `scenario_id` int(11) DEFAULT NULL,
  `sector_code` varchar(10) DEFAULT NULL,
  `sector_name` varchar(255) DEFAULT NULL,
  `sector_agg` varchar(10) DEFAULT NULL,
  `baseline` double DEFAULT NULL,
  `scenario` double DEFAULT NULL,
  `difference` double DEFAULT NULL,
  `reduction` double DEFAULT NULL,
  `percentage_change` double DEFAULT NULL,
  `scenario_history_v21_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_target` (
  `target_id` int(11) NOT NULL,
  `parameter_id` int(11) NOT NULL,
  `energy_type_code` varchar(3) DEFAULT NULL,
  `sector_code` varchar(4) DEFAULT NULL,
  `target_scope` varchar(50) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_target_emission_impact_history` (
  `target_emission_impact_id` int(11) NOT NULL,
  `scenario_history_id` int(11) NOT NULL,
  `parameter_code` varchar(100) DEFAULT NULL,
  `energy_type_code` varchar(3) DEFAULT NULL,
  `sector_code` varchar(4) DEFAULT NULL,
  `sector_name` varchar(128) DEFAULT NULL,
  `target_scope` varchar(50) DEFAULT NULL,
  `target_sector_reduction` double DEFAULT NULL,
  `other_sector_reduction` double DEFAULT NULL,
  `total_reduction` double DEFAULT NULL,
  `target_share_pct` double DEFAULT NULL,
  `other_share_pct` double DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_target_impact_history` (
  `target_impact_id` int(11) NOT NULL,
  `scenario_history_id` int(11) NOT NULL,
  `parameter_code` varchar(100) DEFAULT NULL,
  `energy_type_code` varchar(3) DEFAULT NULL,
  `energy_name` varchar(45) DEFAULT NULL,
  `sector_code` varchar(4) DEFAULT NULL,
  `sector_name` varchar(128) DEFAULT NULL,
  `target_scope` varchar(50) DEFAULT NULL,
  `change_value` double DEFAULT NULL,
  `change_percent` double DEFAULT NULL,
  `baseline_energy` double DEFAULT NULL,
  `scenario_energy` double DEFAULT NULL,
  `energy_difference` double DEFAULT NULL,
  `energy_reduction` double DEFAULT NULL,
  `percentage_change` double DEFAULT NULL,
  `energy_unit` varchar(20) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_type` (
  `scenario_type_id` int(11) NOT NULL,
  `type_code` varchar(50) NOT NULL,
  `type_name` varchar(100) NOT NULL,
  `description` text DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT 1,
  `created_date` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_type_history` (
  `type_history_id` int(11) NOT NULL,
  `scenario_history_id` int(11) NOT NULL,
  `type_code` varchar(50) DEFAULT NULL,
  `type_name` varchar(100) DEFAULT NULL,
  `is_primary` tinyint(1) DEFAULT NULL,
  `scenario_type_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `scenario_type_mapping` (
  `scenario_id` int(11) NOT NULL,
  `scenario_type_id` int(11) NOT NULL,
  `is_primary` tinyint(1) DEFAULT 0,
  `created_date` datetime DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `sector_agg` (
  `id` varchar(6) NOT NULL,
  `name` varchar(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE `sector_lst` (
  `sector_agg` varchar(64) NOT NULL,
  `sector_code` varchar(4) NOT NULL,
  `sector_name` varchar(128) NOT NULL,
  `sector_label` varchar(16) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

ALTER TABLE `co2_factor`
  ADD PRIMARY KEY (`energy_type`);

ALTER TABLE `conversion_factor`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `id_UNIQUE` (`id`);

ALTER TABLE `energy_lst`
  ADD PRIMARY KEY (`energy_type_code`);

ALTER TABLE `fec_byyear`
  ADD PRIMARY KEY (`year`,`energy_type`);

ALTER TABLE `result_emission`
  ADD PRIMARY KEY (`year`,`sector_agg`,`sector_code`,`sector_name`);

ALTER TABLE `result_emission_intensity`
  ADD PRIMARY KEY (`year`,`sector_agg`,`sector_code`,`sector_name`);

ALTER TABLE `scenario_definition`
  ADD PRIMARY KEY (`scenario_id`),
  ADD UNIQUE KEY `uq_scenario_definition_signature` (`scenario_signature`);

ALTER TABLE `scenario_history_v21`
  ADD PRIMARY KEY (`scenario_history_id`),
  ADD KEY `idx_scenario_history_code` (`scenario_code`);

ALTER TABLE `scenario_parameter`
  ADD PRIMARY KEY (`parameter_id`),
  ADD KEY `scenario_id` (`scenario_id`);

ALTER TABLE `scenario_parameter_history`
  ADD PRIMARY KEY (`parameter_history_id`),
  ADD KEY `idx_parameter_history_execution` (`scenario_history_id`);

ALTER TABLE `scenario_sector_result`
  ADD PRIMARY KEY (`id`),
  ADD KEY `fk_sector_result_history_v21` (`scenario_history_v21_id`),
  ADD KEY `idx_sector_result_scenario_id` (`scenario_id`);

ALTER TABLE `scenario_target`
  ADD PRIMARY KEY (`target_id`),
  ADD KEY `parameter_id` (`parameter_id`),
  ADD KEY `energy_type_code` (`energy_type_code`),
  ADD KEY `fk_scenario_target_sector` (`sector_code`);

ALTER TABLE `scenario_target_emission_impact_history`
  ADD PRIMARY KEY (`target_emission_impact_id`),
  ADD KEY `idx_target_emission_impact_history` (`scenario_history_id`);

ALTER TABLE `scenario_target_impact_history`
  ADD PRIMARY KEY (`target_impact_id`),
  ADD KEY `idx_target_impact_history` (`scenario_history_id`);

ALTER TABLE `scenario_type`
  ADD PRIMARY KEY (`scenario_type_id`),
  ADD UNIQUE KEY `type_code` (`type_code`);

ALTER TABLE `scenario_type_history`
  ADD PRIMARY KEY (`type_history_id`),
  ADD KEY `idx_type_history_execution` (`scenario_history_id`);

ALTER TABLE `scenario_type_mapping`
  ADD PRIMARY KEY (`scenario_id`,`scenario_type_id`);

ALTER TABLE `sector_agg`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `id_UNIQUE` (`id`);

ALTER TABLE `sector_lst`
  ADD PRIMARY KEY (`sector_code`),
  ADD UNIQUE KEY `sector_code_UNIQUE` (`sector_code`);

ALTER TABLE `scenario_definition`
  MODIFY `scenario_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=61;

ALTER TABLE `scenario_history_v21`
  MODIFY `scenario_history_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=57;

ALTER TABLE `scenario_parameter`
  MODIFY `parameter_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=76;

ALTER TABLE `scenario_parameter_history`
  MODIFY `parameter_history_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=114;

ALTER TABLE `scenario_sector_result`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=13876;

ALTER TABLE `scenario_target`
  MODIFY `target_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=77;

ALTER TABLE `scenario_target_emission_impact_history`
  MODIFY `target_emission_impact_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=24;

ALTER TABLE `scenario_target_impact_history`
  MODIFY `target_impact_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=28;

ALTER TABLE `scenario_type`
  MODIFY `scenario_type_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=8;

ALTER TABLE `scenario_type_history`
  MODIFY `type_history_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=57;

ALTER TABLE `scenario_parameter`
  ADD CONSTRAINT `scenario_parameter_ibfk_1` FOREIGN KEY (`scenario_id`) REFERENCES `scenario_definition` (`scenario_id`) ON DELETE CASCADE;

ALTER TABLE `scenario_parameter_history`
  ADD CONSTRAINT `fk_parameter_history_execution` FOREIGN KEY (`scenario_history_id`) REFERENCES `scenario_history_v21` (`scenario_history_id`) ON DELETE CASCADE;

ALTER TABLE `scenario_sector_result`
  ADD CONSTRAINT `fk_sector_result_history_v21` FOREIGN KEY (`scenario_history_v21_id`) REFERENCES `scenario_history_v21` (`scenario_history_id`) ON DELETE CASCADE;

ALTER TABLE `scenario_target`
  ADD CONSTRAINT `fk_scenario_target_sector` FOREIGN KEY (`sector_code`) REFERENCES `sector_lst` (`sector_code`),
  ADD CONSTRAINT `scenario_target_ibfk_1` FOREIGN KEY (`parameter_id`) REFERENCES `scenario_parameter` (`parameter_id`) ON DELETE CASCADE,
  ADD CONSTRAINT `scenario_target_ibfk_2` FOREIGN KEY (`energy_type_code`) REFERENCES `energy_lst` (`energy_type_code`);

ALTER TABLE `scenario_target_emission_impact_history`
  ADD CONSTRAINT `fk_target_emission_impact_history` FOREIGN KEY (`scenario_history_id`) REFERENCES `scenario_history_v21` (`scenario_history_id`) ON DELETE CASCADE;

ALTER TABLE `scenario_target_impact_history`
  ADD CONSTRAINT `fk_target_impact_history` FOREIGN KEY (`scenario_history_id`) REFERENCES `scenario_history_v21` (`scenario_history_id`) ON DELETE CASCADE;

ALTER TABLE `scenario_type_history`
  ADD CONSTRAINT `fk_type_history_execution` FOREIGN KEY (`scenario_history_id`) REFERENCES `scenario_history_v21` (`scenario_history_id`) ON DELETE CASCADE;
