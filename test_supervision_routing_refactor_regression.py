from test_create_text_noop_stop_regression import main as create_new_file_success_main
from test_existing_file_improvement_routes_to_patch_regression import main as edit_existing_file_routing_main
from test_permission_done_block_termination_regression import main as blocked_task_stop_main
from test_transform_verified_noop_stop_regression import main as transform_verified_stop_main


def main():
    create_new_file_success_main()
    edit_existing_file_routing_main()
    blocked_task_stop_main()
    transform_verified_stop_main()
    print("OK: supervision routing refactor regression suite passed")


if __name__ == "__main__":
    main()
