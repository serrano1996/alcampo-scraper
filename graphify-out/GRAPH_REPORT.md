# Graph Report - alcampo-scraper  (2026-10-16)

## Corpus Check
- 162 files · ~125,871 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 7 file(s) not represented in the graph (top: (none) 3, .lock 3, .example 1)

## Summary
- 1713 nodes · 5035 edges · 84 communities (60 shown, 24 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 457 edges (avg confidence: 0.91)
- Token cost: 389,977 input · 0 output

## Community Hubs (Navigation)
- Settings and Config
- WAF Cooldown
- Alcampo Session Client
- Retry and Backoff
- Product Service Tests
- Region Repository Memory
- Cursor Pagination
- Upstream Format Resilience
- Lockfile and Infra Tests
- App Wiring Modules
- Integration Test Clients
- Integration Harness Mocks
- Redis Circuit Breaker
- Service Protocols
- Outbound Rate Limiter
- Region Resolution Chain
- Search Mapper Tests
- App Factory and Endpoints
- Region Sessions Registry
- Logging Integration Tests
- Alcampo Raw Models
- Product Search Orchestration
- Domain Exceptions
- Public API Models
- Search Scraper and Gate
- Auth Integration Tests
- Search Scraper Tests
- Logging Config Tests
- Product Mapper
- Raw Product Model Tests
- Traffic Log
- Search Cache
- Readiness Probe Tests
- Rate Limiter Tests
- Products Route Tests
- Outbound Gate Tests
- Logging and Request Context
- In-Flight Coalescing
- Region Service Tests
- Products Route and Dependencies
- Request Context Middleware
- Search Envelope
- Log Param Redaction
- Outbound Pacer Tests
- Outbound Protection E2E
- Constitution and CI
- Region Service
- Dockerfile Contract Tests
- Paginated Search E2E
- Spec 008 Outbound Protection
- Spec 009 Contract Parity
- Spec 007 Region Resolution
- Region Service Doubles
- API Key Validation
- Lifespan and Resources
- Outbound Pacer
- Fase 0 Alcampo Research
- Spec 005 Dockerization
- CI Workflow Tests
- API Key Guard
- Cursor Session Protocol
- Redis Degradation Tests
- Spec 003 Logging
- Spec 001 MVP Decisions
- Term Normalization
- Spec 002 Anti-ban
- Retry Module
- Page Token Redactor
- Docker Compose Services
- Spec 001 Contract
- Product Service Provider
- Raw Product Id Helper
- Traffic Summary
- Lock Script
- Project Root

## God Nodes (most connected - your core abstractions)
1. `UpstreamUnavailableError` - 86 edges
2. `Settings` - 75 edges
3. `AlcampoSearchResponse` - 68 edges
4. `ProductQuery` - 67 edges
5. `WafCooldownRepository` - 55 edges
6. `make_service()` - 55 edges
7. `send_with_retry()` - 54 edges
8. `create_app()` - 53 edges
9. `Region` - 41 edges
10. `OutboundRateLimitedError` - 40 edges

## Surprising Connections (you probably didn't know these)
- `In-process coalescing of identical searches` --semantically_similar_to--> `RegionService`  [INFERRED] [semantically similar]
  specs/008-alcampo-scraper-outbound-protection/spec.md → app/services/region_service.py
- `mypy --strict with Pydantic plugin` --rationale_for--> `AppResources`  [INFERRED]
  specs/006-alcampo-scraper-refactor/spec.md → app/core/state.py
- `Failed field path:type logging (RF-5)` --implements--> `map_search()`  [INFERRED]
  specs/011-alcampo-scraper-upstream-format-resilience/spec.md → app/mappers/product_mapper.py
- `send_with_retry()` --implements--> `Additive retry jitter (RETRY_JITTER_MAX_S)`  [INFERRED]
  app/scrapers/retry.py → specs/002-alcampo-scraper-antibaneo/spec.md
- `RegionService` --implements--> `Postal code to region resolution chain (~8 requests)`  [INFERRED]
  app/services/region_service.py → specs/007-alcampo-scraper-warehouse-resolution/spec.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Upstream error hierarchy (502 family)** — app_exceptions_upstreamunavailableerror, app_exceptions_upstreamblockederror, app_exceptions_cooldownactiveerror [EXTRACTED 1.00]
- **WAF anti-ban defense flow** — docs_investigacion_fase_0_alcampo_aws_waf_challenge, specs_002_alcampo_scraper_antibaneo_spec_waf_cooldown, app_services_waf_cooldown_wafcooldownrepository, app_exceptions_upstreamblockederror, app_services_product_service_productservice, specs_002_alcampo_scraper_antibaneo_spec_retry_jitter [INFERRED 0.85]
- **Product search pipeline** — specs_001_alcampo_scraper_mvp_spec_get_products_endpoint, app_services_product_service_productservice, app_services_search_cache_searchcacherepository, app_scrapers_alcampo_search_alcamposearchscraper, app_scrapers_retry_send_with_retry, app_mappers_product_mapper_map_search [INFERRED 0.85]
- **Outbound protection against Alcampo WAF** — specs_008_alcampo_scraper_outbound_protection_spec_global_rate_limit, specs_008_alcampo_scraper_outbound_protection_spec_growing_cooldown, specs_008_alcampo_scraper_outbound_protection_spec_in_flight_dedup, specs_008_alcampo_scraper_outbound_protection_spec_search_timeout, specs_010_alcampo_scraper_outbound_pacing_spec_long_rate_window, specs_010_alcampo_scraper_outbound_pacing_spec_min_interval_spacing, specs_007_alcampo_scraper_warehouse_resolution_spec_region_resolution_limit [INFERRED 0.85]
- **OutboundGate components** — app_services_outbound_outboundgate, app_services_outbound_outboundpacer, app_services_outbound_trafficlog, app_services_rate_limiter_outboundratelimiter [EXTRACTED 1.00]
- **Region resolution components** — app_scrapers_alcampo_session_alcamposessionclient, app_services_region_repository_regionrepository, app_services_region_service_regionservice, app_services_region_sessions_regionsessions [EXTRACTED 1.00]
- **WAF cooldown correctness across specs 015-017** — specs_015_alcampo_scraper_sibling_review_fixes_spec_waf_cooldown_local_mirror, specs_016_alcampo_scraper_audit_fixes_spec_one_block_one_cooldown, specs_016_alcampo_scraper_audit_fixes_tasks_b1_joining_cooldown_arms_local, specs_017_alcampo_scraper_cooldown_races_spec_set_nx_atomic_start, specs_017_alcampo_scraper_cooldown_races_spec_active_without_last_not_rewritten, specs_017_alcampo_scraper_cooldown_races_spec_pttl_ceil_remaining [INFERRED 0.95]
- **Price unit table grounded in real data** — specs_011_alcampo_scraper_upstream_format_resilience_spec_unknown_price_units_warning, specs_011_alcampo_scraper_upstream_format_resilience_tasks_per_1kg_finding, specs_013_alcampo_scraper_price_units_spec_observed_units_only, specs_013_alcampo_scraper_price_units_spec_per_1kg_kg_suffix, app_mappers_product_mapper_unit_suffixes, specs_013_alcampo_scraper_price_units_plan_alcampo_search_arroz_fixture [INFERRED 0.95]
- **Hash-verified dependency installation** — specs_014_alcampo_scraper_lockfile_spec_hashed_lockfiles, specs_014_alcampo_scraper_lockfile_plan_lock_sh_script, specs_014_alcampo_scraper_lockfile_plan_pip_check_in_ci, specs_015_alcampo_scraper_sibling_review_fixes_spec_build_lock, specs_015_alcampo_scraper_sibling_review_fixes_tasks_lock_sh_injection [INFERRED 0.85]

## Communities (84 total, 24 thin omitted)

### Community 0 - "Settings and Config"
Cohesion: 0.06
Nodes (53): get_settings(), Settings, create_http_client(), clean_env(), set_required(), test_a_non_positive_http_timeout_fails(), test_api_keys_are_hidden_from_repr(), test_api_keys_are_left_out_of_every_dump() (+45 more)

### Community 1 - "WAF Cooldown"
Cohesion: 0.06
Nodes (43): _grow(), LocalCooldown, WafCooldownRepository, activate_in_redis(), in_force(), Plan 016 - Repository audit fixes, Spec 016 - Repository audit fixes, Tasks 016 - Repository audit fixes (+35 more)

### Community 2 - "Alcampo Session Client"
Cohesion: 0.07
Nodes (34): AlcampoSessionClient, HomeState, response_shape(), shape(), test_the_search_response_has_mercadonas_shape(), assert_session_headers(), details(), fixture() (+26 more)

### Community 3 - "Retry and Backoff"
Cohesion: 0.09
Nodes (52): _is_retryable(), _is_waf_challenge(), parse_retry_after(), send_with_retry(), backoff(), rate_limited_wait(), FakeSleep, FakeUniform (+44 more)

### Community 4 - "Product Service Tests"
Cohesion: 0.11
Nodes (55): UpstreamFormatError, ProductQuery, FailingScraper, FakeRegions, FakeScraper, FakeSessions, GatedScraper, HangingScraper (+47 more)

### Community 5 - "Region Repository Memory"
Cohesion: 0.07
Nodes (30): RegionMemory, FakeMonotonic, make_repo(), redis(), test_a_redis_read_fills_the_memory(), test_corrupted_region_record_is_treated_as_missing(), test_forget_region_keeps_a_record_replaced_between_the_read_and_the_delete(), test_forget_region_keeps_the_record_another_process_replaced() (+22 more)

### Community 6 - "Cursor Pagination"
Cohesion: 0.08
Nodes (28): PageOutOfRangeError, estimate_total(), is_last_page(), next_page_token(), page_totals(), PageTotals, PageWalker, ChainedScraper (+20 more)

### Community 7 - "Upstream Format Resilience"
Cohesion: 0.06
Nodes (23): UNIT_SUFFIXES, Plan 011 - Upstream format resilience, Spec 011 - Upstream format resilience, mercadona-scraper (sibling project), Tasks 011 - Upstream format resilience, ChainedScraper broken pages test double, Plan 012 - Operational robustness, Spec 012 - Operational robustness (+15 more)

### Community 8 - "Lockfile and Infra Tests"
Cohesion: 0.07
Nodes (14): config(), resolve(), test_env_file_is_loaded_but_cannot_override_the_redis_url(), pinned(), project(), requirements(), test_every_direct_dependency_is_pinned_in_range_with_hashes(), test_every_line_of_a_lock_is_pinned_with_a_hash() (+6 more)

### Community 10 - "Integration Test Clients"
Cohesion: 0.11
Nodes (12): client(), integration_env(), alcampo(), app_client(), FakeAlcampo, search(), test_a_known_postal_code_costs_no_chain_and_its_region_is_shared(), test_a_postal_code_not_served_is_a_404_and_is_remembered() (+4 more)

### Community 11 - "Integration Harness Mocks"
Cohesion: 0.13
Nodes (21): load_fixture(), mock_alcampo_search(), mock_region_chain(), seed_region(), _text(), test_rejection_happens_before_cache_cooldown_and_alcampo(), test_valid_key_reaches_the_endpoint(), test_alcampo_404_returns_502_with_a_single_call() (+13 more)

### Community 12 - "Redis Circuit Breaker"
Cohesion: 0.14
Nodes (15): RedisCircuitBreaker, RedisCircuitOpenError, FakeClock, Operation, records(), test_a_closed_circuit_runs_the_operation(), test_a_failure_opens_the_circuit_once_and_is_raised(), test_a_probe_ending_without_a_redis_answer_lets_the_next_call_probe() (+7 more)

### Community 13 - "Service Protocols"
Cohesion: 0.09
Nodes (9): RegionFinder, SessionProvider, _holds(), _postal_code_key(), Region, _region_key(), RegionRepository, delete_if_unchanged() (+1 more)

### Community 14 - "Outbound Rate Limiter"
Cohesion: 0.11
Nodes (9): OutboundRateLimitedError, LocalRateLimiter, OutboundRateLimiter, disabled_limiter(), test_a_refusal_from_redis_closes_a_probing_circuit(), test_limiters_sharing_a_fallback_share_its_quota(), test_release_also_works_on_the_local_fallback(), test_the_local_fallback_slides_and_rejections_do_not_consume_quota() (+1 more)

### Community 15 - "Region Resolution Chain"
Cohesion: 0.10
Nodes (6): AlcampoAreaDetails, ChainSession, CooldownGate, ResolutionLimiter, SessionRegistry, FakeSession

### Community 16 - "Search Mapper Tests"
Cohesion: 0.22
Nodes (23): map_search(), envelope(), first_raw_product(), load_fixture(), mapper_records(), product(), test_a_field_failing_in_several_products_is_named_once(), test_a_product_missing_the_image_key_is_discarded() (+15 more)

### Community 17 - "App Factory and Endpoints"
Cohesion: 0.11
Nodes (10): resources(), create_app(), ready(), test_openapi_exposes_the_products_route_with_its_parameters(), test_openapi_exposes_the_response_schemas(), test_resources_outside_the_lifespan_fail_clearly(), test_the_lifespan_stores_every_resource_in_one_typed_object(), DownRedis (+2 more)

### Community 18 - "Region Sessions Registry"
Cohesion: 0.13
Nodes (4): _Entry, HomeView, RegionSession, RegionSessions

### Community 19 - "Logging Integration Tests"
Cohesion: 0.18
Nodes (17): app_records(), request_records(), test_a_streamed_response_is_finished_only_after_its_body(), chunks(), stream(), test_alcampo_cookies_never_reach_the_logs(), test_an_error_after_the_response_started_is_logged_and_re_raised(), chunks() (+9 more)

### Community 20 - "Alcampo Raw Models"
Cohesion: 0.14
Nodes (12): AlcampoActiveSession, AlcampoAdditionalPageInfo, AlcampoArea, AlcampoCartProposition, AlcampoCategoryCount, AlcampoDeliverability, AlcampoDeliveryAddress, AlcampoImage (+4 more)

### Community 21 - "Product Search Orchestration"
Cohesion: 0.14
Nodes (5): ProductSearchResponse, SearchScraper, _for_query(), ProductService, keep()

### Community 22 - "Domain Exceptions"
Cohesion: 0.19
Nodes (11): AlcampoScraperError, CooldownActiveError, UpstreamBlockedError, UpstreamThrottledError, UpstreamUnavailableError, test_cooldown_active_error_is_an_upstream_unavailable_error(), test_foreseen_degradations_share_a_throttled_parent(), test_postal_code_not_served_is_a_domain_error_but_not_an_upstream_failure() (+3 more)

### Community 23 - "Public API Models"
Cohesion: 0.11
Nodes (14): Product, SearchMetadata, test_empty_postal_code_raises(), test_image_url_and_category_cannot_be_null(), test_invalid_term_raises(), test_page_and_page_size_default_to_the_first_page_of_50(), test_page_or_page_size_out_of_range_raises(), test_postal_code_is_stripped_before_validation() (+6 more)

### Community 24 - "Search Scraper and Gate"
Cohesion: 0.11
Nodes (7): AlcampoSearchScraper, OutboundGate, Pacer, SlotLimiter, test_search_passes_the_configured_jitter_to_retries(), fake_send_with_retry(), RecordingGate

### Community 25 - "Auth Integration Tests"
Cohesion: 0.23
Nodes (15): anon_client(), auth_warnings(), start_client(), startup_warnings(), test_invalid_key_is_logged_without_its_value(), test_key_sent_in_the_url_is_redacted_from_app_logs(), test_missing_key_is_logged_with_the_path(), test_no_configured_keys_rejects_everyone() (+7 more)

### Community 26 - "Search Scraper Tests"
Cohesion: 0.25
Nodes (17): load_fixture(), make_scraper(), scraper_errors(), test_exhausted_limit_sends_nothing_to_alcampo(), test_limit_exhausted_between_attempts_stops_the_retries(), test_non_json_body_is_logged_as_invalid_json(), test_search_calls_alcampo_exactly_once_with_expected_params(), test_search_raises_on_json_without_expected_shape() (+9 more)

### Community 27 - "Logging Config Tests"
Cohesion: 0.20
Nodes (11): configure_logging(), httpx_logger(), lines(), restore_logging_state(), test_caplog_keeps_capturing_after_configuring(), test_configuring_twice_does_not_duplicate_lines(), test_configuring_twice_installs_one_redactor(), test_level_filters_lower_records() (+3 more)

### Community 28 - "Product Mapper"
Cohesion: 0.18
Nodes (12): _failed_fields(), format_unit_price(), map_product(), AlcampoUnitPrice, first_raw_product(), test_format_unit_price_known_units(), test_format_unit_price_none_returns_none(), test_format_unit_price_unknown_unit_returns_none() (+4 more)

### Community 29 - "Raw Product Model Tests"
Cohesion: 0.18
Nodes (15): AlcampoProduct, first_raw_product(), load_fixture(), real_products_with(), test_invalid_price_amount_raises(), test_last_page_and_missing_counts_are_optional(), test_no_results_fixture_has_empty_product_groups(), test_product_ignores_unknown_fields() (+7 more)

### Community 30 - "Traffic Log"
Cohesion: 0.21
Nodes (8): TrafficLog, clock(), FakeClock, test_an_empty_log_reports_zeros(), test_entries_older_than_15_minutes_are_dropped(), test_only_client_errors_are_counted_as_4xx(), test_requests_are_counted_by_kind_and_window(), test_the_summary_reads_as_one_log_field()

### Community 31 - "Search Cache"
Cohesion: 0.27
Nodes (13): _cache_key(), SearchCacheRepository, make_response(), redis(), test_case_and_spacing_variants_share_one_entry(), test_corrupted_value_is_treated_as_a_miss(), test_corrupted_value_logs_a_warning_with_the_key(), test_each_page_and_page_size_has_its_own_entry() (+5 more)

### Community 32 - "Readiness Probe Tests"
Cohesion: 0.19
Nodes (11): test_an_open_circuit_answers_without_trying_redis(), test_not_ready_without_redis_and_still_alive(), test_ready_with_redis(), warnings(), CountingBrokenRedis, opened_breaker(), skipped_quietly(), test_the_cooldown_uses_its_fallback_while_open() (+3 more)

### Community 33 - "Rate Limiter Tests"
Cohesion: 0.26
Nodes (15): clock(), FakeClock, make_limiter(), redis(), test_a_disabled_limit_returns_no_id_and_release_does_nothing(), test_a_refusal_holds_even_if_giving_the_slot_back_fails(), test_a_rejected_attempt_does_not_consume_quota(), test_acquire_returns_an_id_and_release_gives_the_slot_back() (+7 more)

### Community 34 - "Products Route Tests"
Cohesion: 0.32
Nodes (15): FakeService, make_client(), make_response(), records_at(), test_cooldown_502_is_only_a_warning(), test_missing_term_returns_422(), test_page_out_of_range_returns_404_logged_as_info(), test_postal_code_not_served_returns_404_logged_as_info() (+7 more)

### Community 35 - "Outbound Gate Tests"
Cohesion: 0.18
Nodes (9): FakeLimiter, FakePacer, make_gate(), Steps, test_a_rejection_by_the_long_window_never_reaches_the_short_one(), test_a_rejection_by_the_short_window_gives_the_long_slot_back(), test_a_request_is_paced_then_checked_against_both_windows(), test_an_unexpected_client_error_is_a_warning_with_its_context() (+1 more)

### Community 37 - "In-Flight Coalescing"
Cohesion: 0.26
Nodes (8): InFlightSearches, GatedFetch, settle(), test_a_finished_key_is_forgotten(), test_an_error_reaches_every_waiter(), test_cancelling_one_waiter_does_not_cancel_the_others(), test_different_keys_are_not_grouped(), test_simultaneous_runs_of_one_key_share_a_single_fetch()

### Community 38 - "Region Service Tests"
Cohesion: 0.24
Nodes (13): Harness, records(), test_a_forgotten_region_is_resolved_again(), test_a_resolution_past_the_timeout_fails_and_closes_its_session(), test_a_resolution_slower_than_a_search_but_within_its_own_timeout_succeeds(), test_a_waf_challenge_propagates_and_nothing_is_cached(), test_cached_postal_code_needs_no_request(), test_exhausted_resolution_limit_stops_before_creating_a_destination() (+5 more)

### Community 40 - "Request Context Middleware"
Cohesion: 0.13
Nodes (4): RequestContextMiddleware, Plan 006 - Refactor and Quality, Spec 006 - Refactor and Quality, Tasks 006 - Refactor and Quality

### Community 42 - "Log Param Redaction"
Cohesion: 0.24
Nodes (11): _is_secret_name(), params_for_log(), redact_params(), test_a_name_containing_a_secret_marker_is_redacted(), test_a_repeated_param_keeps_every_value_in_order(), test_params_without_secrets_are_unchanged(), test_secret_named_params_are_redacted_case_insensitively(), test_separators_and_unicode_variants_do_not_hide_a_marker() (+3 more)

### Community 43 - "Outbound Pacer Tests"
Cohesion: 0.25
Nodes (9): wait_before_retry(), FakeTime, pacer(), test_concurrent_requests_leave_one_after_another(), test_the_first_request_does_not_wait(), test_the_next_request_waits_the_minimum_plus_jitter(), test_time_already_elapsed_counts(), test_zero_minimum_disables_the_spacing() (+1 more)

### Community 44 - "Outbound Protection E2E"
Cohesion: 0.30
Nodes (7): limited_client(), records(), test_a_cache_hit_is_logged_with_its_own_request_id(), test_a_challenge_logs_what_the_gate_actually_sent(), test_a_recent_second_challenge_doubles_the_cooldown(), test_an_exhausted_long_window_rejects_new_searches(), test_cooldown_is_checked_before_the_rate_limit()

### Community 45 - "Constitution and CI"
Cohesion: 0.18
Nodes (5): CI workflow (ci.yml), CI checks job (ruff, format, mypy, pytest, docker build), Project Constitution, AWS WAF challenge (202 + x-amzn-waf-action), WAF cooldown after challenge (WAF_COOLDOWN_SECONDS)

### Community 46 - "Region Service"
Cohesion: 0.22
Nodes (3): PostalCodeNotServedError, RegionResolutionLimitedError, RegionService

### Community 47 - "Dockerfile Contract Tests"
Cohesion: 0.25
Nodes (11): after_last_from(), args_of(), instructions(), raw_text(), test_healthcheck_probes_health_on_ipv4_loopback(), test_installs_the_pinned_dependencies_then_the_project_alone(), test_installs_the_project_without_dev_dependencies(), test_no_secrets_are_referenced() (+3 more)

### Community 48 - "Paginated Search E2E"
Cohesion: 0.28
Nodes (6): mock_paged_search(), test_a_page_past_the_last_one_is_404(), test_a_repeated_page_comes_from_the_cache(), test_pages_in_order_cost_one_request_each(), test_without_parameters_the_first_page_of_50(), tokens_sent()

### Community 49 - "Spec 008 Outbound Protection"
Cohesion: 0.26
Nodes (5): AWS WAF of Alcampo, Plan 008 - Outbound Protection to Alcampo, Spec 008 - Outbound Protection to Alcampo, Tasks 008 - Outbound Protection to Alcampo, Spec 010 - Outbound Pacing to Alcampo

### Community 50 - "Spec 009 Contract Parity"
Cohesion: 0.20
Nodes (5): mercadona-scraper (sibling project), Plan 009 - Contract Parity with Mercadona and Pagination, Spec 009 - Contract Parity with Mercadona and Pagination, Tasks 009 - Contract Parity with Mercadona and Pagination, Mercadona search response schema fixture

### Community 51 - "Spec 007 Region Resolution"
Cohesion: 0.20
Nodes (3): Plan 007 - Postal Code Region Resolution, Spec 007 - Postal Code Region Resolution, Tasks 007 - Postal Code Region Resolution

### Community 52 - "Region Service Doubles"
Cohesion: 0.18
Nodes (3): FakeCooldown, FakeLimiter, FakeSessions

### Community 53 - "API Key Validation"
Cohesion: 0.33
Nodes (6): is_valid_api_key(), test_any_configured_key_is_valid(), test_anything_else_is_invalid(), test_no_configured_keys_rejects_everything(), test_non_ascii_candidate_is_rejected_without_error(), test_non_ascii_configured_key_works()

### Community 54 - "Lifespan and Resources"
Cohesion: 0.22
Nodes (5): AppResources, create_redis(), lifespan(), new_session(), test_redis_client_has_connect_and_operation_timeouts()

### Community 55 - "Outbound Pacer"
Cohesion: 0.20
Nodes (3): OutboundPacer, Plan 010 - Outbound Pacing to Alcampo, Tasks 010 - Outbound Pacing to Alcampo

### Community 56 - "Fase 0 Alcampo Research"
Cohesion: 0.29
Nodes (8): Fase 0 - Live Alcampo Research, X-CSRF-Token read from home HTML, Default anonymous region Vaguada (retailerRegionId 5), Ocado platform (compraonline.alcampo.es), Region set by session cookies (postal code -> region chain), Ocado search endpoint /api/webproductpagews/v6/product-pages/search, Alcampo home fixture (Telde), Alcampo home fixture (Vaguada)

### Community 57 - "Spec 005 Dockerization"
Cohesion: 0.27
Nodes (4): Plan 005 - Dockerization, Spec 005 - Dockerization, Tasks 005 - Dockerization, tests/infra (infrastructure tests)

### Community 58 - "CI Workflow Tests"
Cohesion: 0.24
Nodes (4): commands(), test_installs_the_pinned_dev_dependencies(), test_runs_every_check_that_closes_a_task(), workflow()

### Community 59 - "API Key Guard"
Cohesion: 0.25
Nodes (4): require_api_key(), Plan 004 Authentication, API_KEYS frozenset setting (repr=False), Tasks 004 Authentication

### Community 62 - "Spec 003 Logging"
Cohesion: 0.38
Nodes (6): README alcampo-scraper, Plan 003 Logging, LOG_LEVEL validation in Settings, Spec 003 Logging, Tasks 003 Logging, Spec 004 Authentication

### Community 65 - "Term Normalization"
Cohesion: 0.33
Nodes (3): sent_term(), normalize_term(), test_normalize_term()

### Community 66 - "Spec 002 Anti-ban"
Cohesion: 0.40
Nodes (5): Plan 002 Anti-ban, User-Agent pool chosen once per client, Spec 002 Anti-ban measures, Additive retry jitter (RETRY_JITTER_MAX_S), Tasks 002 Anti-ban

### Community 70 - "Spec 001 Contract"
Cohesion: 0.67
Nodes (4): Spec 001 MVP product search, GET /api/v1/products, ProductSearchResponse contract (same as mercadona-scraper), Tasks 001 MVP

## Knowledge Gaps
- **11 isolated node(s):** `alcampo-scraper`, `CI workflow (ci.yml)`, `LOG_LEVEL validation in Settings`, `Tasks 005 - Dockerization`, `Tasks 006 - Refactor and Quality` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 476 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **24 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `UpstreamUnavailableError` connect `Domain Exceptions` to `Settings and Config`, `Alcampo Session Client`, `Retry and Backoff`, `Product Service Tests`, `Region Repository Memory`, `Cursor Pagination`, `App Wiring Modules`, `App Factory and Endpoints`, `Region Sessions Registry`, `Alcampo Raw Models`, `Product Search Orchestration`, `Search Scraper and Gate`, `Search Scraper Tests`, `Rate Limiter Tests`, `Products Route Tests`, `Region Service Tests`, `Search Envelope`, `Region Service`, `Spec 001 MVP Decisions`, `Retry Module`?**
  _High betweenness centrality (0.085) - this node is a cross-community bridge._
- **Are the 41 inferred relationships involving `UpstreamUnavailableError` (e.g. with `create_app()` and `AlcampoSearchScraper`) actually correct?**
  _`UpstreamUnavailableError` has 41 INFERRED edges - model-reasoned connections that need verification._
- **What connects `alcampo-scraper`, `CI workflow (ci.yml)`, `LOG_LEVEL validation in Settings` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Settings and Config` be split into smaller, more focused modules?**
  _Cohesion score 0.0602655771195097 - nodes in this community are weakly interconnected._
- **Why does `Settings` connect `Settings and Config` to `WAF Cooldown`, `Alcampo Session Client`, `Product Service Tests`, `Region Service Tests`, `Upstream Format Resilience`, `App Wiring Modules`, `Region Service`, `Region Resolution Chain`, `Alcampo Raw Models`, `Product Search Orchestration`, `Lifespan and Resources`, `Region Service Doubles`, `Search Scraper and Gate`, `Search Scraper Tests`?**
  _High betweenness centrality (0.068) - this node is a cross-community bridge._
- **Are the 21 inferred relationships involving `Settings` (e.g. with `AppResources` and `AlcampoSearchScraper`) actually correct?**
  _`Settings` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Should `WAF Cooldown` be split into smaller, more focused modules?**
  _Cohesion score 0.05512016718913271 - nodes in this community are weakly interconnected._