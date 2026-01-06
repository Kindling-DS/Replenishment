from config.settings import logger

def main():
    logger.info("Pipeline started")

    from scripts import download_replenishment
    from scripts import process_replenishment

    download_replenishment.run()
    process_replenishment.run()

    logger.info("Pipeline finished successfully")

if __name__ == "__main__":
    main()
