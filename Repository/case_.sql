USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[case_]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[case_](
	[case_id] [uniqueidentifier] NOT NULL,
	[chorus_quote_number] [nvarchar](20) NULL,
	[chorus_gapq] [nvarchar](20) NULL,
	[created_at] [datetime2](7) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[case_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[case_] ADD  DEFAULT (sysutcdatetime()) FOR [created_at]
GO
