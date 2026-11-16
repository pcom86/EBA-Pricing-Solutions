USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[scheme]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[scheme](
	[scheme_id] [uniqueidentifier] NOT NULL,
	[internal_gla_code] [nvarchar](50) NOT NULL,
	[quote_number] [int] NULL,
	[quote_type] [nvarchar](50) NULL,
	[quote_group] [nvarchar](50) NULL,
	[scheme_name] [nvarchar](200) NULL,
	[doer] [nvarchar](100) NULL,
	[checker] [nvarchar](100) NULL,
	[intermediary] [nvarchar](100) NULL,
	[broker_name] [nvarchar](100) NULL,
	[client_manager] [nvarchar](100) NULL,
	[branch] [nvarchar](100) NULL,
	[region] [nvarchar](100) NULL,
	[salary_definition] [nvarchar](50) NULL,
	[premium_frequency] [nvarchar](50) NULL,
	[commencement_date] [date] NULL,
	[calculation_date] [date] NULL,
	[ctr_fingerprint] [nvarchar](64) NOT NULL,
	[ctr_path] [nvarchar](500) NULL,
	[created_at] [datetime2](7) NOT NULL,
	[quote_seq] [int] NULL,
	[case_id] [uniqueidentifier] NULL,
	[scheme_business_key] [nvarchar](200) NULL,
PRIMARY KEY CLUSTERED 
(
	[scheme_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
UNIQUE NONCLUSTERED 
(
	[ctr_fingerprint] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[scheme] ADD  DEFAULT (sysutcdatetime()) FOR [created_at]
GO
ALTER TABLE [dbo].[scheme]  WITH CHECK ADD  CONSTRAINT [FK_scheme_case] FOREIGN KEY([case_id])
REFERENCES [dbo].[case_] ([case_id])
GO
ALTER TABLE [dbo].[scheme] CHECK CONSTRAINT [FK_scheme_case]
GO
