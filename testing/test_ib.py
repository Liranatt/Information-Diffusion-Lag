import asyncio
import logging
from ib_async import IB, util

util.patchAsyncio()
logging.basicConfig(level=logging.INFO)

async def main():
    ib = IB()
    print("Connecting...")
    try:
        await ib.connectAsync('192.168.1.159', 4004, clientId=2, timeout=10)
        print(f"Connected! Managed accounts: {ib.managedAccounts()}")
        
        print("Sleeping for 5 seconds to see if Warning 2110 appears...")
        await asyncio.sleep(5)
        
        print("Requesting account summary...")
        summary = await ib.accountSummaryAsync()
        print(f"Got {len(summary)} summary rows.")
        
        print("Requesting positions...")
        positions = await ib.reqPositionsAsync()
        print(f"Got {len(positions)} positions.")
        
    except Exception as e:
        print(f"Failed: {e}")
    finally:
        ib.disconnect()

asyncio.run(main())
